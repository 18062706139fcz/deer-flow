"""Managed Lark/Feishu CLI integration support.

The integration installs the official ``lark-*`` AI-agent skills into the
current user's read-only managed integration skill directory. It deliberately
does not use the ordinary custom-skill archive path: this is a trusted,
versioned first-party integration package, not user-authored mutable content.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from deerflow.config.app_config import AppConfig
from deerflow.config.paths import get_paths
from deerflow.skills.installer import is_executable_binary_prefix, is_symlink_member, is_unsafe_zip_member
from deerflow.skills.parser import parse_skill_file
from deerflow.skills.permissions import make_skill_tree_sandbox_readable
from deerflow.skills.types import SKILL_MD_FILE, SkillCategory

INTEGRATION_ID = "lark-cli"
DEFAULT_LARK_CLI_VERSION = "v1.0.65"
DEFAULT_LARK_CLI_ARCHIVE_URL = f"https://github.com/larksuite/cli/archive/refs/tags/{DEFAULT_LARK_CLI_VERSION}.zip"
LARK_CLI_SOURCE_ARCHIVE_ENV = "DEER_FLOW_LARK_CLI_SKILLS_ARCHIVE"
LARK_CLI_DOWNLOAD_TIMEOUT_SECONDS = 60
LARK_HTTP_TIMEOUT_SECONDS = 20
LARK_CONFIG_POLL_TIMEOUT_SECONDS = 45
LARK_CLI_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
LARK_CLI_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
LARK_CLI_MANIFEST_FILE = ".deerflow-lark-cli-manifest.json"
_DEERFLOW_LARK_SHARED_GUIDANCE_MARKER = "<!-- deerflow-lark-cli-auth-guidance-v2 -->"
_DEERFLOW_LARK_SHARED_GUIDANCE_LEGACY_MARKERS = ("<!-- deerflow-lark-cli-auth-guidance-v1 -->",)
_LARK_APP_REGISTRATION_PATH = "/oauth/v1/app/registration"

LARK_SKILL_NAMES: tuple[str, ...] = (
    "lark-approval",
    "lark-apps",
    "lark-attendance",
    "lark-base",
    "lark-calendar",
    "lark-contact",
    "lark-doc",
    "lark-drive",
    "lark-event",
    "lark-im",
    "lark-mail",
    "lark-markdown",
    "lark-minutes",
    "lark-note",
    "lark-okr",
    "lark-openapi-explorer",
    "lark-shared",
    "lark-sheets",
    "lark-skill-maker",
    "lark-slides",
    "lark-task",
    "lark-vc",
    "lark-vc-agent",
    "lark-whiteboard",
    "lark-wiki",
    "lark-workflow-meeting-summary",
    "lark-workflow-standup-report",
)
LARK_SKILL_NAME_SET = frozenset(LARK_SKILL_NAMES)


@dataclass(frozen=True)
class LarkCliProbe:
    available: bool
    path: str | None = None
    version: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class LarkAuthProbe:
    status: str
    message: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class LarkIntegrationStatus:
    installed: bool
    version: str
    manifest_version: str | None
    app_configured: bool
    app_id: str | None
    app_brand: str | None
    skills_expected: int
    skills_installed: int
    installed_skills: tuple[str, ...]
    enabled_skills: tuple[str, ...]
    install_path: str
    cli: LarkCliProbe
    auth: LarkAuthProbe


@dataclass(frozen=True)
class LarkInstallResult:
    success: bool
    installed_skills: tuple[str, ...]
    status: LarkIntegrationStatus
    message: str


@dataclass(frozen=True)
class LarkConfigStartResult:
    verification_url: str
    device_code: str
    expires_in: int | None = None
    interval: int | None = None
    user_code: str | None = None
    brand: str = "feishu"


@dataclass(frozen=True)
class LarkConfigCompleteResult:
    success: bool
    status: LarkIntegrationStatus
    message: str


@dataclass(frozen=True)
class LarkAuthStartResult:
    verification_url: str
    device_code: str
    expires_in: int | None = None
    user_code: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class LarkAuthCompleteResult:
    success: bool
    status: LarkIntegrationStatus
    message: str


def lark_integration_root(user_id: str) -> Path:
    """Return the user-scoped root for managed Lark skills."""
    return get_paths().user_integration_skills_dir(user_id) / INTEGRATION_ID


def lark_manifest_path(user_id: str) -> Path:
    return lark_integration_root(user_id) / LARK_CLI_MANIFEST_FILE


def lark_cli_config_dir(user_id: str) -> Path:
    return get_paths().user_dir(user_id) / "integrations" / INTEGRATION_ID / "config"


def lark_cli_data_dir(user_id: str) -> Path:
    return get_paths().user_dir(user_id) / "integrations" / INTEGRATION_ID / "data"


def lark_cli_env(user_id: str) -> dict[str, str]:
    """Environment for Gateway-side lark-cli probes.

    The directories are per-user so a local trusted-mode login cannot bleed
    across accounts. Auth Proxy support can later replace these directories for
    sandbox execution without changing the status API contract.
    """
    config_dir = lark_cli_config_dir(user_id)
    data_dir = lark_cli_data_dir(user_id)
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "LARKSUITE_CLI_CONFIG_DIR": str(config_dir),
        "LARKSUITE_CLI_DATA_DIR": str(data_dir),
        "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
        "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
    }


def probe_lark_cli() -> LarkCliProbe:
    path = _resolve_lark_cli_path()
    if path is None:
        return LarkCliProbe(available=False, error="lark-cli is not on PATH")
    try:
        result = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001 - probe boundary
        return LarkCliProbe(available=False, path=path, error=str(exc))

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return LarkCliProbe(available=False, path=path, error=output or f"exit code {result.returncode}")
    return LarkCliProbe(available=True, path=path, version=output or None)


def probe_lark_auth(user_id: str) -> LarkAuthProbe:
    path = _resolve_lark_cli_path()
    if path is None:
        return LarkAuthProbe(status="unavailable", message="lark-cli is not installed on the Gateway")
    app_config = read_lark_app_config(user_id)
    if not app_config["configured"]:
        return LarkAuthProbe(status="not_configured", message="Lark app is not configured")
    try:
        result = subprocess.run(
            [path, "auth", "status", "--json", "--verify"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env=lark_cli_env(user_id),
        )
    except subprocess.TimeoutExpired:
        return LarkAuthProbe(status="error", message="lark-cli auth status timed out")
    except Exception as exc:  # noqa: BLE001 - probe boundary
        return LarkAuthProbe(status="error", message=str(exc))

    raw = (result.stdout or result.stderr or "").strip()
    data: dict[str, Any] | None = None
    if raw:
        try:
            parsed = json.loads(raw)
            data = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            data = None

    if result.returncode != 0:
        message = _auth_error_message(data) if data else raw
        return LarkAuthProbe(status="not_authorized", message=message or "Lark user authorization is not configured")

    user = None
    if data:
        identities = data.get("identities")
        if isinstance(identities, dict):
            user_info = identities.get("user")
            if isinstance(user_info, dict):
                user = str(user_info.get("userName") or user_info.get("openId") or "") or None
        if user is None and data.get("userName"):
            user = str(data["userName"])
    return LarkAuthProbe(status="authenticated", user=user, message="lark-cli auth is configured")


def get_lark_integration_status(user_id: str, config: AppConfig) -> LarkIntegrationStatus:
    root = lark_integration_root(user_id)
    manifest = _read_manifest(root)
    app_config = read_lark_app_config(user_id)
    installed_skills = tuple(sorted(_installed_lark_skill_names(root)))
    enabled_skills = tuple(sorted(_enabled_lark_skill_names(user_id, config)))
    return LarkIntegrationStatus(
        installed=bool(manifest) and "lark-shared" in installed_skills,
        version=DEFAULT_LARK_CLI_VERSION,
        manifest_version=str(manifest.get("version")) if manifest else None,
        app_configured=bool(app_config["configured"]),
        app_id=app_config["app_id"],
        app_brand=app_config["brand"],
        skills_expected=len(LARK_SKILL_NAMES),
        skills_installed=len(installed_skills),
        installed_skills=installed_skills,
        enabled_skills=enabled_skills,
        install_path=str(root),
        cli=probe_lark_cli(),
        auth=probe_lark_auth(user_id),
    )


def read_lark_app_config(user_id: str) -> dict[str, str | bool | None]:
    config_path = lark_cli_config_dir(user_id) / "config.json"
    if not config_path.is_file():
        return {"configured": False, "app_id": None, "brand": None}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"configured": False, "app_id": None, "brand": None}
    if not isinstance(data, dict):
        return {"configured": False, "app_id": None, "brand": None}
    apps = data.get("apps")
    if not isinstance(apps, list) or not apps:
        return {"configured": False, "app_id": None, "brand": None}
    current = data.get("currentApp")
    app = None
    if isinstance(current, str) and current:
        app = next((candidate for candidate in apps if isinstance(candidate, dict) and (candidate.get("name") == current or candidate.get("appId") == current)), None)
    if app is None:
        app = apps[0] if isinstance(apps[0], dict) else None
    if not isinstance(app, dict):
        return {"configured": False, "app_id": None, "brand": None}
    app_id = str(app.get("appId") or "").strip()
    app_secret = app.get("appSecret")
    brand = str(app.get("brand") or "feishu").strip() or "feishu"
    return {"configured": bool(app_id and app_secret), "app_id": app_id or None, "brand": brand}


def install_lark_integration(
    user_id: str,
    config: AppConfig,
    *,
    source_archive: str | Path | None = None,
) -> LarkInstallResult:
    archive_path = Path(source_archive) if source_archive is not None else _resolve_or_download_archive()
    created_temp_archive = source_archive is None and not os.getenv(LARK_CLI_SOURCE_ARCHIVE_ENV)

    try:
        installed_skills = _install_lark_skills_from_archive(user_id, archive_path)
    finally:
        if created_temp_archive:
            try:
                archive_path.unlink(missing_ok=True)
            except OSError:
                pass

    status = get_lark_integration_status(user_id, config)
    return LarkInstallResult(
        success=True,
        installed_skills=installed_skills,
        status=status,
        message=f"Installed {len(installed_skills)} Lark/Feishu skills.",
    )


def start_lark_config(user_id: str, *, brand: str = "feishu") -> LarkConfigStartResult:
    """Start the browser flow that creates/binds a Lark OAuth app for this user."""
    parsed_brand = _normalize_lark_brand(brand)
    begin_data = _request_lark_app_registration_begin(parsed_brand)
    user_code = str(begin_data.get("user_code") or "").strip()
    device_code = str(begin_data.get("device_code") or "").strip()
    if not user_code or not device_code:
        raise ValueError("Lark app registration did not return a user_code and device_code.")
    verification_url = _build_lark_config_verification_url(parsed_brand, user_code)
    return LarkConfigStartResult(
        verification_url=verification_url,
        device_code=device_code,
        expires_in=_int_or_none(begin_data.get("expires_in")),
        interval=_int_or_none(begin_data.get("interval")),
        user_code=user_code,
        brand=parsed_brand,
    )


def complete_lark_config(
    user_id: str,
    config: AppConfig,
    *,
    device_code: str,
    brand: str = "feishu",
    interval: int | None = None,
    expires_in: int | None = None,
) -> LarkConfigCompleteResult:
    """Complete app registration and persist app credentials through lark-cli."""
    device_code = device_code.strip()
    if not device_code:
        raise ValueError("device_code is required.")
    parsed_brand = _normalize_lark_brand(brand)
    result = _poll_lark_app_registration(
        device_code=device_code,
        brand=parsed_brand,
        interval=interval or 5,
        expires_in=expires_in or 300,
    )
    if not result.get("client_secret") and _tenant_brand(result) == "lark":
        result = _poll_lark_app_registration(
            device_code=device_code,
            brand="lark",
            interval=interval or 5,
            expires_in=expires_in or 300,
        )

    app_id = str(result.get("client_id") or "").strip()
    app_secret = str(result.get("client_secret") or "").strip()
    final_brand = _tenant_brand(result) or parsed_brand
    if not app_id or not app_secret:
        raise ValueError("Lark app registration succeeded but did not return app credentials.")

    _save_lark_app_config_with_cli(user_id, app_id=app_id, app_secret=app_secret, brand=final_brand)
    status = get_lark_integration_status(user_id, config)
    return LarkConfigCompleteResult(
        success=True,
        status=status,
        message="Lark/Feishu connection setup completed.",
    )


def start_lark_auth(
    user_id: str,
    *,
    domains: tuple[str, ...] = (),
    scope: str | None = None,
    recommend: bool = True,
) -> LarkAuthStartResult:
    """Start a non-blocking Lark device authorization flow.

    The returned URL is safe to show in the browser UI or in a chat message.
    ``device_code`` must be sent back to :func:`complete_lark_auth` after the
    user finishes authorization in Lark/Feishu.
    """
    path = _require_lark_cli_path()
    args = [path, "auth", "login", "--no-wait", "--json"]
    if recommend:
        args.append("--recommend")
    if scope:
        args.extend(["--scope", scope])
    for domain in domains:
        if domain:
            args.extend(["--domain", domain])

    data = _run_lark_cli_json(args, user_id=user_id, timeout=20)
    verification_url = str(data.get("verification_url") or data.get("verification_uri_complete") or "").strip()
    device_code = str(data.get("device_code") or "").strip()
    if not verification_url or not device_code:
        raise ValueError("lark-cli did not return a verification_url and device_code.")

    return LarkAuthStartResult(
        verification_url=verification_url,
        device_code=device_code,
        expires_in=_int_or_none(data.get("expires_in")),
        user_code=str(data.get("user_code") or "") or None,
        hint=str(data.get("hint") or "") or None,
    )


def complete_lark_auth(user_id: str, config: AppConfig, *, device_code: str) -> LarkAuthCompleteResult:
    """Complete a Lark device authorization flow after the user approves it."""
    device_code = device_code.strip()
    if not device_code:
        raise ValueError("device_code is required.")

    path = _require_lark_cli_path()
    _run_lark_cli_json(
        [path, "auth", "login", "--device-code", device_code, "--json"],
        user_id=user_id,
        timeout=45,
        allow_empty_success=True,
    )
    status = get_lark_integration_status(user_id, config)
    return LarkAuthCompleteResult(
        success=status.auth.status == "authenticated",
        status=status,
        message="Lark/Feishu authorization completed." if status.auth.status == "authenticated" else (status.auth.message or "Lark/Feishu authorization status is still pending."),
    )


def _resolve_lark_cli_path() -> str | None:
    return shutil.which("lark-cli")


def _require_lark_cli_path() -> str:
    path = _resolve_lark_cli_path()
    if path is None:
        raise FileNotFoundError("lark-cli is not on PATH. Rebuild the Gateway image with @larksuite/cli installed.")
    return path


def _normalize_lark_brand(brand: str) -> str:
    return "lark" if brand.strip().lower() == "lark" else "feishu"


def _lark_endpoints(brand: str) -> dict[str, str]:
    if _normalize_lark_brand(brand) == "lark":
        return {
            "open": "https://open.larksuite.com",
            "accounts": "https://accounts.larksuite.com",
        }
    return {
        "open": "https://open.feishu.cn",
        "accounts": "https://accounts.feishu.cn",
    }


def _request_lark_app_registration_begin(brand: str) -> dict[str, Any]:
    # lark-cli uses the Feishu accounts endpoint for the begin step, then
    # switches to the tenant brand only if the poll response indicates Lark.
    accounts_url = _lark_endpoints("feishu")["accounts"] + _LARK_APP_REGISTRATION_PATH
    body = urllib.parse.urlencode(
        {
            "action": "begin",
            "archetype": "PersonalAgent",
            "auth_method": "client_secret",
            "request_user_info": "open_id tenant_brand",
        }
    ).encode("utf-8")
    data = _post_lark_form(accounts_url, body)
    if "error" in data:
        raise ValueError(str(data.get("error_description") or data.get("error") or "Lark app registration failed."))
    return data


def _build_lark_config_verification_url(brand: str, user_code: str) -> str:
    base = f"{_lark_endpoints(brand)['open']}/page/cli"
    query = urllib.parse.urlencode(
        {
            "user_code": user_code,
            "lpv": DEFAULT_LARK_CLI_VERSION,
            "ocv": DEFAULT_LARK_CLI_VERSION,
            "from": "cli",
        }
    )
    return f"{base}?{query}"


def _poll_lark_app_registration(
    *,
    device_code: str,
    brand: str,
    interval: int,
    expires_in: int,
) -> dict[str, Any]:
    accounts_url = _lark_endpoints(brand)["accounts"] + _LARK_APP_REGISTRATION_PATH
    deadline = time.monotonic() + min(max(expires_in, 1), LARK_CONFIG_POLL_TIMEOUT_SECONDS)
    poll_interval = max(min(interval, 10), 1)
    last_error = "authorization_pending"
    while time.monotonic() < deadline:
        body = urllib.parse.urlencode({"action": "poll", "device_code": device_code}).encode("utf-8")
        data = _post_lark_form(accounts_url, body)
        if not data.get("error") and data.get("client_id"):
            return data
        error = str(data.get("error") or "")
        last_error = str(data.get("error_description") or error or "Lark app registration is still pending.")
        if error == "authorization_pending":
            time.sleep(poll_interval)
            continue
        if error == "slow_down":
            poll_interval = min(poll_interval + 5, 30)
            time.sleep(poll_interval)
            continue
        raise ValueError(last_error)
    raise TimeoutError(f"Lark app registration is still pending: {last_error}")


def _post_lark_form(url: str, body: bytes) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LARK_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - network boundary
        raise ValueError(f"Lark app registration request failed: {exc}") from exc
    parsed = _parse_json_object(raw)
    if parsed is None:
        raise ValueError("Lark app registration returned non-JSON response.")
    return parsed


def _tenant_brand(result: dict[str, Any]) -> str | None:
    user_info = result.get("user_info")
    if not isinstance(user_info, dict):
        return None
    brand = str(user_info.get("tenant_brand") or "").strip().lower()
    return brand if brand in {"feishu", "lark"} else None


def _save_lark_app_config_with_cli(user_id: str, *, app_id: str, app_secret: str, brand: str) -> None:
    path = _require_lark_cli_path()
    try:
        result = subprocess.run(
            [path, "config", "init", "--app-id", app_id, "--app-secret-stdin", "--brand", _normalize_lark_brand(brand)],
            input=app_secret + "\n",
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=lark_cli_env(user_id),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Timed out while saving Lark connection setup.") from exc
    if result.returncode != 0:
        raw = (result.stderr or result.stdout or "").strip()
        parsed = _parse_json_object(raw)
        message = _auth_error_message(parsed) if parsed else raw
        raise ValueError(message or f"lark-cli config init exited with code {result.returncode}")


def _run_lark_cli_json(
    args: list[str],
    *,
    user_id: str,
    timeout: int,
    allow_empty_success: bool = False,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=lark_cli_env(user_id),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Timed out waiting for Lark/Feishu authorization. Complete authorization in the browser, then try again.") from exc

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    raw = stdout or stderr
    parsed = _parse_json_object(raw)

    if result.returncode != 0:
        message = _auth_error_message(parsed) if parsed else raw
        raise ValueError(message or f"lark-cli exited with code {result.returncode}")

    if not raw and allow_empty_success:
        return {}
    if parsed is None:
        if allow_empty_success:
            return {}
        raise ValueError(raw or "lark-cli did not return JSON output.")
    return parsed


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _auth_error_message(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    error = data.get("error")
    if isinstance(error, dict):
        for key in ("message", "hint", "type"):
            value = error.get(key)
            if value:
                return str(value)
    for key in ("message", "msg", "hint"):
        value = data.get(key)
        if value:
            return str(value)
    return None


def _read_manifest(root: Path) -> dict[str, Any] | None:
    path = root / LARK_CLI_MANIFEST_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _installed_lark_skill_names(root: Path) -> set[str]:
    names: set[str] = set()
    if not root.is_dir():
        return names
    for skill_name in LARK_SKILL_NAMES:
        if (root / skill_name / SKILL_MD_FILE).is_file():
            names.add(skill_name)
    return names


def _enabled_lark_skill_names(user_id: str, config: AppConfig) -> set[str]:
    from deerflow.skills.storage import get_or_new_user_skill_storage

    try:
        storage = get_or_new_user_skill_storage(user_id, app_config=config)
        return {skill.name for skill in storage.load_skills(enabled_only=True) if skill.name in LARK_SKILL_NAME_SET}
    except Exception:
        return set()


def _resolve_or_download_archive() -> Path:
    if env_archive := os.getenv(LARK_CLI_SOURCE_ARCHIVE_ENV):
        return Path(env_archive)

    fd, archive_name = tempfile.mkstemp(prefix="lark-cli-skills-", suffix=".zip")
    os.close(fd)
    archive_path = Path(archive_name)
    try:
        with urllib.request.urlopen(DEFAULT_LARK_CLI_ARCHIVE_URL, timeout=LARK_CLI_DOWNLOAD_TIMEOUT_SECONDS) as response:
            total = 0
            with archive_path.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > LARK_CLI_MAX_ARCHIVE_BYTES:
                        raise ValueError("Lark CLI source archive is too large.")
                    out.write(chunk)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def _install_lark_skills_from_archive(user_id: str, archive_path: Path) -> tuple[str, ...]:
    if not archive_path.is_file():
        raise FileNotFoundError(f"Lark CLI skills archive not found: {archive_path}")

    parent = get_paths().user_integration_skills_dir(user_id)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / INTEGRATION_ID
    staging_parent = Path(tempfile.mkdtemp(prefix=".installing-lark-cli-", dir=str(parent)))
    staging_target = staging_parent / INTEGRATION_ID
    staging_target.mkdir(parents=True, exist_ok=True)

    backup: Path | None = None
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            extracted = _extract_lark_skills(zf, staging_target)
        _validate_extracted_lark_skills(staging_target, extracted)
        _append_deerflow_lark_shared_guidance(staging_target)
        _write_manifest(staging_target, extracted)
        make_skill_tree_sandbox_readable(staging_target)

        if target.exists():
            backup = parent / f".replacing-{INTEGRATION_ID}-{os.getpid()}"
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
        staging_target.rename(target)
        if backup is not None:
            shutil.rmtree(backup)
        return tuple(sorted(extracted))
    except Exception:
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def _extract_lark_skills(zf: zipfile.ZipFile, destination: Path) -> set[str]:
    extracted: set[str] = set()
    total_written = 0
    dest_root = destination.resolve()

    for info in zf.infolist():
        if info.is_dir():
            continue
        if is_unsafe_zip_member(info) or is_symlink_member(info):
            raise ValueError(f"Unsafe Lark CLI archive member: {info.filename!r}")

        skill_name, relative = _resolve_lark_skill_member(info.filename)
        if skill_name is None or relative is None:
            continue

        target = dest_root / skill_name / relative
        if not target.resolve().is_relative_to(dest_root):
            raise ValueError(f"Archive member escapes destination: {info.filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)

        with zf.open(info) as src, target.open("wb") as out:
            first_chunk = True
            while chunk := src.read(65536):
                if first_chunk and is_executable_binary_prefix(chunk):
                    raise ValueError(f"Archive contains executable binary member: {info.filename!r}")
                first_chunk = False
                total_written += len(chunk)
                if total_written > LARK_CLI_MAX_EXTRACTED_BYTES:
                    raise ValueError("Lark CLI skills archive expands to too much data.")
                out.write(chunk)
        extracted.add(skill_name)

    return extracted


def _resolve_lark_skill_member(raw_name: str) -> tuple[str | None, Path | None]:
    normalized = posixpath.normpath(raw_name.replace("\\", "/"))
    if normalized in {"", "."} or normalized.startswith("../"):
        return None, None
    parts = PurePosixPath(normalized).parts

    if "skills" in parts:
        idx = parts.index("skills")
        if len(parts) <= idx + 2:
            return None, None
        skill_name = parts[idx + 1]
        rel_parts = parts[idx + 2 :]
    elif parts and parts[0] in LARK_SKILL_NAME_SET:
        skill_name = parts[0]
        rel_parts = parts[1:]
    else:
        return None, None

    if skill_name not in LARK_SKILL_NAME_SET or not rel_parts:
        return None, None
    if any(part in {"", ".", ".."} for part in rel_parts):
        raise ValueError(f"Unsafe Lark skill archive member: {raw_name!r}")
    return skill_name, Path(*rel_parts)


def _validate_extracted_lark_skills(root: Path, extracted: set[str]) -> None:
    missing = sorted(set(LARK_SKILL_NAMES) - extracted)
    if missing:
        raise ValueError(f"Lark CLI archive is missing required skills: {', '.join(missing)}")

    for skill_name in LARK_SKILL_NAMES:
        skill_file = root / skill_name / SKILL_MD_FILE
        parsed = parse_skill_file(skill_file, SkillCategory.INTEGRATION, relative_path=Path(INTEGRATION_ID) / skill_name)
        if parsed is None:
            raise ValueError(f"Invalid Lark skill metadata: {skill_name}/{SKILL_MD_FILE}")
        if parsed.name != skill_name:
            raise ValueError(f"Lark skill directory {skill_name!r} declares name {parsed.name!r}")


def _append_deerflow_lark_shared_guidance(root: Path) -> None:
    skill_file = root / "lark-shared" / SKILL_MD_FILE
    content = skill_file.read_text(encoding="utf-8")
    if _DEERFLOW_LARK_SHARED_GUIDANCE_MARKER in content:
        return
    for legacy_marker in _DEERFLOW_LARK_SHARED_GUIDANCE_LEGACY_MARKERS:
        if legacy_marker in content:
            content = content.split(legacy_marker, maxsplit=1)[0].rstrip()
            break
    guidance = f"""

{_DEERFLOW_LARK_SHARED_GUIDANCE_MARKER}

## DeerFlow 授权入口

在 DeerFlow 中，如果 `lark-cli auth status` 或业务命令提示未配置、未登录、token 过期或缺少用户授权：

1. 不要要求用户在终端执行 `lark-cli config init`、`lark-cli auth login` 或 `lark-cli auth login --device-code`。
2. 回复用户这个可点击链接：[打开飞书授权设置](?settings=integrations)。
3. 告诉用户在 **Settings → Integrations → Lark / Feishu CLI** 点击“连接飞书”，在浏览器里完成授权后再回来继续当前任务。
4. 如果错误中包含缺失的 `scope`、`permission_violations` 或建议的 `--domain`，告诉用户在该设置页选择对应权限域（例如日历选择 Calendar），或把具体 scope 填入“Exact OAuth scope / 具体 OAuth scope”后重新授权。

只有在用户明确说明已经完成授权后，才继续调用具体的 `lark-cli` 业务命令。
"""
    skill_file.write_text(content.rstrip() + guidance + "\n", encoding="utf-8")


def _write_manifest(root: Path, installed_skills: set[str]) -> None:
    manifest = {
        "provider": INTEGRATION_ID,
        "version": DEFAULT_LARK_CLI_VERSION,
        "source": DEFAULT_LARK_CLI_ARCHIVE_URL,
        "installed_at": datetime.now(UTC).isoformat(),
        "skills": sorted(installed_skills),
    }
    (root / LARK_CLI_MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

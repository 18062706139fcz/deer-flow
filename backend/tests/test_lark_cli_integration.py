from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import integrations as integrations_router
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli
from deerflow.sandbox.tools import _lark_cli_env_from_runtime
from deerflow.skills.storage import reset_skill_storage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from deerflow.skills.types import SkillCategory


def _skill_content(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name} integration skill\n---\n\n# {name}\n"


def _make_lark_cli_source_zip(tmp_path: Path, *, omit_skill: str | None = None, renamed_skill: str | None = None) -> Path:
    archive = tmp_path / "lark-cli.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for skill_name in lark_cli.LARK_SKILL_NAMES:
            if skill_name == omit_skill:
                continue
            declared_name = f"{skill_name}-renamed" if skill_name == renamed_skill else skill_name
            zf.writestr(f"cli-1.0.65/skills/{skill_name}/SKILL.md", _skill_content(declared_name))
            zf.writestr(f"cli-1.0.65/skills/{skill_name}/references/readme.md", f"# {skill_name}\n")
    return archive


def _assert_lark_root_missing(user_id: str) -> None:
    root = lark_cli.lark_integration_root(user_id)
    assert not root.exists()


def _config(skills_root: Path):
    return SimpleNamespace(
        skills=SimpleNamespace(
            get_skills_path=lambda: skills_root,
            container_path="/mnt/skills",
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        )
    )


def _patch_paths(monkeypatch, base_dir: Path) -> None:
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=base_dir))


def test_install_lark_integration_installs_readonly_user_scoped_skills(monkeypatch, tmp_path):
    reset_skill_storage()
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    archive = _make_lark_cli_source_zip(tmp_path)

    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/usr/bin/lark-cli", version="v1.0.65"))
    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id, **_kwargs: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))

    result = lark_cli.install_lark_integration("alice", config, source_archive=archive)

    assert result.success is True
    assert "lark-doc" in result.installed_skills
    assert result.status.installed is True
    root = lark_cli.lark_integration_root("alice")
    assert (root / "lark-doc" / "SKILL.md").is_file()
    assert (root / lark_cli.LARK_CLI_MANIFEST_FILE).is_file()
    shared_content = (root / "lark-shared" / "SKILL.md").read_text(encoding="utf-8")
    assert "?settings=integrations" in shared_content
    assert "不要要求用户在终端执行" in shared_content
    assert "Exact OAuth scope" in shared_content

    storage = UserScopedSkillStorage("alice", host_path=str(skills_root), app_config=config)
    skills = storage.load_skills(enabled_only=False)
    lark_doc = next(skill for skill in skills if skill.name == "lark-doc")
    assert lark_doc.category == SkillCategory.INTEGRATION
    assert lark_doc.get_container_file_path("/mnt/skills") == "/mnt/skills/integrations/lark-cli/lark-doc/SKILL.md"
    assert lark_doc.enabled is True
    reset_skill_storage()


def test_install_lark_integration_is_idempotent_across_reinstalls(monkeypatch, tmp_path):
    reset_skill_storage()
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    archive = _make_lark_cli_source_zip(tmp_path)

    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/usr/bin/lark-cli", version="v1.0.65"))
    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id, **_kwargs: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))

    first = lark_cli.install_lark_integration("alice", config, source_archive=archive)
    root = lark_cli.lark_integration_root("alice")
    # Drop a stray file so the reinstall must replace the whole tree, not merge.
    stray = root / "lark-doc" / "stray.txt"
    stray.write_text("stale", encoding="utf-8")

    second = lark_cli.install_lark_integration("alice", config, source_archive=archive)

    assert first.installed_skills == second.installed_skills
    assert second.status.installed is True
    assert (root / "lark-doc" / "SKILL.md").is_file()
    assert not stray.exists()
    # No leftover backup/staging dirs beside the target after a reinstall.
    parent = root.parent
    leftovers = [p.name for p in parent.iterdir() if p.name != lark_cli.INTEGRATION_ID]
    assert leftovers == []
    reset_skill_storage()


def test_install_lark_integration_succeeds_when_backup_cleanup_fails(monkeypatch, tmp_path):
    reset_skill_storage()
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    archive = _make_lark_cli_source_zip(tmp_path)

    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/usr/bin/lark-cli", version="v1.0.65"))
    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id, **_kwargs: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))

    # First install lays down the target so the reinstall has a backup to clean.
    lark_cli.install_lark_integration("alice", config, source_archive=archive)

    real_rmtree = shutil.rmtree
    forced_raises = {"count": 0}

    def _rmtree(path, *args, **kwargs):
        # The post-rename backup deletion is best-effort and now passes
        # ignore_errors=True, so a transient FS error there must not flip a
        # successful install into a failure. Force any rmtree that does *not*
        # ignore errors to raise, proving the success path no longer depends on
        # a fragile backup cleanup.
        if kwargs.get("ignore_errors"):
            return real_rmtree(path, *args, **kwargs)
        forced_raises["count"] += 1
        raise OSError("transient FS error during backup cleanup")

    monkeypatch.setattr(lark_cli.shutil, "rmtree", _rmtree)

    result = lark_cli.install_lark_integration("alice", config, source_archive=archive)

    assert result.success is True
    root = lark_cli.lark_integration_root("alice")
    assert (root / "lark-doc" / "SKILL.md").is_file()
    # No non-ignoring rmtree is relied upon on the success path, and no leftover
    # backup dir remains beside the target after the reinstall.
    assert forced_raises["count"] == 0
    leftovers = [p.name for p in root.parent.iterdir() if p.name != lark_cli.INTEGRATION_ID]
    assert leftovers == []
    reset_skill_storage()


def test_install_lark_integration_records_content_sha_in_manifest(monkeypatch, tmp_path):
    reset_skill_storage()
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    archive = _make_lark_cli_source_zip(tmp_path)

    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/usr/bin/lark-cli", version="v1.0.65"))
    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id, **_kwargs: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))

    lark_cli.install_lark_integration("alice", config, source_archive=archive)

    manifest = json.loads((lark_cli.lark_integration_root("alice") / lark_cli.LARK_CLI_MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["version"] == "v1.0.65"
    assert isinstance(manifest["content_sha256"], str)
    assert len(manifest["content_sha256"]) == 64
    reset_skill_storage()


def test_install_lark_integration_reports_content_change_on_reinstall(monkeypatch, tmp_path):
    reset_skill_storage()
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    archive = _make_lark_cli_source_zip(tmp_path)

    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/usr/bin/lark-cli", version="v1.0.65"))
    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id, **_kwargs: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))

    first = lark_cli.install_lark_integration("alice", config, source_archive=archive)
    assert "content changed" not in first.message

    changed_dir = tmp_path / "changed"
    changed_dir.mkdir()
    changed_archive = _make_lark_cli_source_zip(changed_dir)
    with zipfile.ZipFile(changed_archive, "a") as zf:
        zf.writestr("cli-1.0.65/skills/lark-doc/references/extra.md", "# extra content\n")

    second = lark_cli.install_lark_integration("alice", config, source_archive=changed_archive)
    assert "content changed" in second.message
    reset_skill_storage()


def test_install_lark_integration_rejects_zip_slip_member(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    config = _config(tmp_path / "skills")
    archive = _make_lark_cli_source_zip(tmp_path)
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("../evil.txt", "escape")

    with pytest.raises(ValueError, match="Unsafe Lark CLI archive member"):
        lark_cli.install_lark_integration("alice", config, source_archive=archive)

    _assert_lark_root_missing("alice")


def test_install_lark_integration_rejects_symlink_member(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    config = _config(tmp_path / "skills")
    archive = _make_lark_cli_source_zip(tmp_path)
    link_info = zipfile.ZipInfo("cli-1.0.65/skills/lark-doc/references/link")
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr(link_info, "target")

    with pytest.raises(ValueError, match="Unsafe Lark CLI archive member"):
        lark_cli.install_lark_integration("alice", config, source_archive=archive)

    _assert_lark_root_missing("alice")


def test_install_lark_integration_rejects_executable_binary_member(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    config = _config(tmp_path / "skills")
    archive = _make_lark_cli_source_zip(tmp_path)
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("cli-1.0.65/skills/lark-doc/bin/tool", b"\x7fELFbinary")

    with pytest.raises(ValueError, match="executable binary member"):
        lark_cli.install_lark_integration("alice", config, source_archive=archive)

    _assert_lark_root_missing("alice")


def test_install_lark_integration_rejects_oversized_extraction(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    config = _config(tmp_path / "skills")
    archive = _make_lark_cli_source_zip(tmp_path)
    monkeypatch.setattr(lark_cli, "LARK_CLI_MAX_EXTRACTED_BYTES", 128)

    with pytest.raises(ValueError, match="expands to too much data"):
        lark_cli.install_lark_integration("alice", config, source_archive=archive)

    _assert_lark_root_missing("alice")


def test_install_lark_integration_rejects_missing_required_skill(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    config = _config(tmp_path / "skills")
    archive = _make_lark_cli_source_zip(tmp_path, omit_skill="lark-doc")

    with pytest.raises(ValueError, match="missing required skills: lark-doc"):
        lark_cli.install_lark_integration("alice", config, source_archive=archive)

    _assert_lark_root_missing("alice")


def test_install_lark_integration_rejects_renamed_skill_metadata(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    config = _config(tmp_path / "skills")
    archive = _make_lark_cli_source_zip(tmp_path, renamed_skill="lark-doc")

    with pytest.raises(ValueError, match="declares name 'lark-doc-renamed'"):
        lark_cli.install_lark_integration("alice", config, source_archive=archive)

    _assert_lark_root_missing("alice")


def test_fallback_and_docker_lark_cli_versions_match():
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    match = re.search(r"^ARG LARK_CLI_NPM_VERSION=(?P<version>\S+)$", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)

    assert match is not None
    assert lark_cli.LARK_CLI_NPM_VERSION == match.group("version")
    assert lark_cli.FALLBACK_LARK_CLI_VERSION == f"v{lark_cli.LARK_CLI_NPM_VERSION}"


def test_resolve_lark_cli_path_prefers_managed_gateway_cli(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    managed_bin = lark_cli.lark_cli_managed_gateway_dir() / "node_modules" / ".bin" / "lark-cli"
    managed_bin.parent.mkdir(parents=True)
    managed_bin.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(lark_cli.shutil, "which", lambda _name: "/usr/bin/lark-cli")

    assert lark_cli._resolve_lark_cli_path() == str(managed_bin)


def test_install_managed_gateway_lark_cli_uses_deerflow_prefix(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    captured: dict[str, object] = {}

    monkeypatch.setattr(lark_cli.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    def _run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        managed_bin = lark_cli.lark_cli_managed_gateway_dir() / "node_modules" / ".bin" / "lark-cli"
        managed_bin.parent.mkdir(parents=True)
        managed_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(lark_cli.subprocess, "run", _run)
    monkeypatch.setattr(lark_cli, "_probe_lark_cli_at_path", lambda path: lark_cli.LarkCliProbe(available=True, path=path, version="lark-cli VERSION 1.2.3"))

    result = lark_cli._install_managed_gateway_lark_cli("v1.2.3")

    assert result.available is True
    assert result.version == "lark-cli VERSION 1.2.3"
    assert captured["args"] == [
        "/usr/bin/npm",
        "install",
        "--prefix",
        str(lark_cli.lark_cli_managed_gateway_dir()),
        "--no-audit",
        "--no-fund",
        "@larksuite/cli@1.2.3",
    ]


def test_install_lark_integration_installs_managed_gateway_cli_before_skill_pack(monkeypatch, tmp_path):
    reset_skill_storage()
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    archive = _make_lark_cli_source_zip(tmp_path)
    downloaded_versions: list[str] = []

    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id, **_kwargs: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))
    monkeypatch.setattr(lark_cli, "_ensure_managed_gateway_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/managed/bin/lark-cli", version="lark-cli VERSION 9.9.9"))

    def _download(version: str) -> Path:
        downloaded_versions.append(version)
        return archive

    monkeypatch.setattr(lark_cli, "_download_lark_archive", _download)

    result = lark_cli.install_lark_integration("alice", config)

    assert downloaded_versions == ["v9.9.9"]
    assert result.status.manifest_version == "v9.9.9"
    reset_skill_storage()


def test_resolve_latest_lark_cli_version_uses_release_tag(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"tag_name": "v1.2.3"}).encode("utf-8")

    monkeypatch.setattr(lark_cli.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert lark_cli._resolve_latest_lark_cli_version() == "v1.2.3"


def test_resolve_latest_lark_cli_version_falls_back_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(lark_cli.urllib.request, "urlopen", _boom)
    assert lark_cli._resolve_latest_lark_cli_version() == lark_cli.FALLBACK_LARK_CLI_VERSION


def test_lark_archive_url_rejects_invalid_version_tag():
    with pytest.raises(ValueError, match="Invalid Lark CLI version tag"):
        lark_cli._lark_archive_url("v1.2.3/../../evil")


def test_start_lark_auth_returns_browser_url(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    captured: dict[str, object] = {}

    def _run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "verification_url": "https://open.feishu.cn/auth/mock",
                    "device_code": "device-code",
                    "expires_in": 600,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(lark_cli.shutil, "which", lambda _name: "/usr/bin/lark-cli")
    monkeypatch.setattr(lark_cli.subprocess, "run", _run)

    result = lark_cli.start_lark_auth("alice", domains=("calendar",), recommend=True)

    assert result.verification_url == "https://open.feishu.cn/auth/mock"
    assert result.device_code == "device-code"
    assert captured["args"] == [
        "/usr/bin/lark-cli",
        "auth",
        "login",
        "--no-wait",
        "--json",
        "--recommend",
        "--domain",
        "calendar",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["LARKSUITE_CLI_CONFIG_DIR"].endswith("users/alice/integrations/lark-cli/config")


def test_start_lark_auth_uses_minimal_login_by_default(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    captured: dict[str, object] = {}

    def _run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "verification_url": "https://open.feishu.cn/auth/mock",
                    "device_code": "device-code",
                    "expires_in": 600,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(lark_cli.shutil, "which", lambda _name: "/usr/bin/lark-cli")
    monkeypatch.setattr(lark_cli.subprocess, "run", _run)

    result = lark_cli.start_lark_auth("alice")

    assert result.verification_url == "https://open.feishu.cn/auth/mock"
    assert captured["args"] == [
        "/usr/bin/lark-cli",
        "auth",
        "login",
        "--no-wait",
        "--json",
    ]


def test_lark_cli_env_from_runtime_exposes_settings_auth_to_lark_commands(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    runtime = SimpleNamespace(context={"user_id": "alice"})

    env = _lark_cli_env_from_runtime(runtime, "lark-cli auth status --json", sandbox_paths=False)

    assert env is not None
    assert env["LARKSUITE_CLI_CONFIG_DIR"].endswith("users/alice/integrations/lark-cli/config")
    assert env["LARKSUITE_CLI_DATA_DIR"].endswith("users/alice/integrations/lark-cli/data")


def test_lark_cli_env_from_runtime_uses_container_paths_for_sandbox_lark_commands():
    runtime = SimpleNamespace(context={"user_id": "alice"})

    env = _lark_cli_env_from_runtime(runtime, "/usr/bin/lark-cli auth status", sandbox_paths=True)

    assert env is not None
    assert env["LARKSUITE_CLI_CONFIG_DIR"] == lark_cli.LARK_CLI_SANDBOX_CONFIG_DIR
    assert env["LARKSUITE_CLI_DATA_DIR"] == lark_cli.LARK_CLI_SANDBOX_DATA_DIR


def test_lark_cli_env_from_runtime_ignores_non_lark_commands(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path / "home")
    runtime = SimpleNamespace(context={"user_id": "alice"})

    assert _lark_cli_env_from_runtime(runtime, "echo hello", sandbox_paths=False) is None


def test_complete_lark_auth_polls_device_code_and_returns_status(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    captured: dict[str, object] = {}

    def _run_lark_cli_json(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(lark_cli, "_resolve_lark_cli_path", lambda: "/usr/bin/lark-cli")
    monkeypatch.setattr(lark_cli, "_run_lark_cli_json", _run_lark_cli_json)
    monkeypatch.setattr(
        lark_cli,
        "get_lark_integration_status",
        lambda _user_id, _config, **_kwargs: lark_cli.LarkIntegrationStatus(
            installed=True,
            version="v1.0.65",
            manifest_version="v1.0.65",
            latest_available_version=None,
            runtime_version_mismatch=False,
            app_configured=True,
            app_id="cli_mock",
            app_brand="feishu",
            skills_expected=27,
            skills_installed=27,
            installed_skills=("lark-doc",),
            enabled_skills=("lark-doc",),
            install_path="/tmp/lark",
            cli=lark_cli.LarkCliProbe(available=True),
            auth=lark_cli.LarkAuthProbe(status="authenticated", user="Alice"),
        ),
    )

    result = lark_cli.complete_lark_auth("alice", config, device_code="device-code")

    assert result.success is True
    assert captured["args"] == [
        "/usr/bin/lark-cli",
        "auth",
        "login",
        "--device-code",
        "device-code",
        "--json",
    ]
    assert captured["kwargs"] == {
        "user_id": "alice",
        "timeout": 45,
        "allow_empty_success": True,
    }


def test_start_lark_config_returns_app_registration_url(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(
        lark_cli,
        "_request_lark_app_registration_begin",
        lambda _brand: {
            "user_code": "abc",
            "device_code": "config-device-code",
            "expires_in": 600,
            "interval": 5,
        },
    )

    result = lark_cli.start_lark_config("alice", brand="feishu")

    assert result.device_code == "config-device-code"
    assert result.user_code == "abc"
    assert result.verification_url.startswith("https://open.feishu.cn/page/cli?")
    assert "user_code=abc" in result.verification_url


def test_complete_lark_config_saves_app_credentials_and_returns_status(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        lark_cli,
        "_poll_lark_app_registration",
        lambda **_kwargs: {
            "client_id": "cli_mock",
            "client_secret": "secret",
            "user_info": {"tenant_brand": "feishu"},
        },
    )
    monkeypatch.setattr(
        lark_cli,
        "_save_lark_app_config_with_cli",
        lambda user_id, **kwargs: captured.update({"user_id": user_id, **kwargs}),
    )
    monkeypatch.setattr(
        lark_cli,
        "get_lark_integration_status",
        lambda _user_id, _config, **_kwargs: lark_cli.LarkIntegrationStatus(
            installed=True,
            version="v1.0.65",
            manifest_version="v1.0.65",
            latest_available_version=None,
            runtime_version_mismatch=False,
            app_configured=True,
            app_id="cli_mock",
            app_brand="feishu",
            skills_expected=27,
            skills_installed=27,
            installed_skills=("lark-doc",),
            enabled_skills=("lark-doc",),
            install_path="/tmp/lark",
            cli=lark_cli.LarkCliProbe(available=True),
            auth=lark_cli.LarkAuthProbe(status="not_authorized", user=None),
        ),
    )

    result = lark_cli.complete_lark_config("alice", config, device_code="config-device-code", brand="feishu")

    assert result.success is True
    assert captured == {
        "user_id": "alice",
        "app_id": "cli_mock",
        "app_secret": "secret",
        "brand": "feishu",
    }


def test_complete_lark_config_repolls_lark_tenant_for_client_secret(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path / "home")
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir()
    config = _config(skills_root)
    poll_calls: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    def _poll_lark_app_registration(**kwargs):
        poll_calls.append(kwargs)
        if kwargs["brand"] == "feishu":
            return {
                "client_id": "cli_mock",
                "user_info": {"tenant_brand": "lark"},
            }
        return {
            "client_id": "cli_mock",
            "client_secret": "secret",
            "user_info": {"tenant_brand": "lark"},
        }

    monkeypatch.setattr(lark_cli, "_poll_lark_app_registration", _poll_lark_app_registration)
    monkeypatch.setattr(
        lark_cli,
        "_save_lark_app_config_with_cli",
        lambda user_id, **kwargs: captured.update({"user_id": user_id, **kwargs}),
    )
    monkeypatch.setattr(
        lark_cli,
        "get_lark_integration_status",
        lambda _user_id, _config, **_kwargs: lark_cli.LarkIntegrationStatus(
            installed=True,
            version="v1.0.65",
            manifest_version="v1.0.65",
            latest_available_version=None,
            runtime_version_mismatch=False,
            app_configured=True,
            app_id="cli_mock",
            app_brand="lark",
            skills_expected=27,
            skills_installed=27,
            installed_skills=("lark-doc",),
            enabled_skills=("lark-doc",),
            install_path="/tmp/lark",
            cli=lark_cli.LarkCliProbe(available=True),
            auth=lark_cli.LarkAuthProbe(status="not_authorized", user=None),
        ),
    )

    result = lark_cli.complete_lark_config("alice", config, device_code="config-device-code", brand="feishu")

    assert result.success is True
    assert [call["brand"] for call in poll_calls] == ["feishu", "lark"]
    assert captured == {
        "user_id": "alice",
        "app_id": "cli_mock",
        "app_secret": "secret",
        "brand": "lark",
    }


def _make_user(system_role: str) -> User:
    return User(email=f"{system_role}-integration@example.com", password_hash="x", system_role=system_role, id=uuid4())


def _make_app(*, system_role: str, config):
    app = make_authed_test_app(user_factory=lambda: _make_user(system_role))
    app.dependency_overrides[get_config] = lambda: config
    app.include_router(integrations_router.router)
    return app


def test_lark_install_requires_admin(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)

    def _should_not_install(*args, **kwargs):
        raise AssertionError("install should be admin-gated")

    monkeypatch.setattr(integrations_router, "install_lark_integration", _should_not_install)

    with TestClient(app) as client:
        response = client.post("/api/integrations/lark/install")

    assert response.status_code == 403


def test_lark_status_is_available_to_authenticated_users(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)

    monkeypatch.setattr(
        integrations_router,
        "get_lark_integration_status",
        lambda _user_id, _config, **_kwargs: lark_cli.LarkIntegrationStatus(
            installed=False,
            version="v1.0.65",
            manifest_version=None,
            latest_available_version=None,
            runtime_version_mismatch=False,
            app_configured=False,
            app_id=None,
            app_brand=None,
            skills_expected=27,
            skills_installed=0,
            installed_skills=(),
            enabled_skills=(),
            install_path="/tmp/lark-cli",
            cli=lark_cli.LarkCliProbe(available=False, error="missing"),
            auth=lark_cli.LarkAuthProbe(status="unavailable", message="missing"),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/integrations/lark/status")

    assert response.status_code == 200
    assert response.json()["installed"] is False


def test_lark_config_start_route_returns_browser_url(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)

    monkeypatch.setattr(
        integrations_router,
        "start_lark_config",
        lambda _user_id, **_kwargs: lark_cli.LarkConfigStartResult(
            verification_url="https://open.feishu.cn/page/cli?user_code=config",
            device_code="config-device-code",
            expires_in=600,
            interval=5,
            user_code="config",
            brand="feishu",
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/integrations/lark/config/start", json={"brand": "feishu"})

    assert response.status_code == 200
    assert response.json()["verification_url"] == "https://open.feishu.cn/page/cli?user_code=config"
    assert response.json()["device_code"] == "config-device-code"


def test_lark_config_complete_route_saves_app_credentials(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)

    monkeypatch.setattr(
        integrations_router,
        "complete_lark_config",
        lambda _user_id, _config, *, device_code, **_kwargs: lark_cli.LarkConfigCompleteResult(
            success=True,
            message=f"configured {device_code}",
            status=lark_cli.LarkIntegrationStatus(
                installed=True,
                version="v1.0.65",
                manifest_version="v1.0.65",
                latest_available_version=None,
                runtime_version_mismatch=False,
                app_configured=True,
                app_id="cli_mock",
                app_brand="feishu",
                skills_expected=27,
                skills_installed=27,
                installed_skills=("lark-doc",),
                enabled_skills=("lark-doc",),
                install_path="/tmp/lark",
                cli=lark_cli.LarkCliProbe(available=True),
                auth=lark_cli.LarkAuthProbe(status="not_authorized", user=None),
            ),
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/integrations/lark/config/complete",
            json={"device_code": "config-device-code", "brand": "feishu", "interval": 5, "expires_in": 600},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"]["app_configured"] is True


def test_lark_auth_start_route_returns_browser_url(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)
    captured_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        integrations_router,
        "start_lark_auth",
        lambda _user_id, **kwargs: (
            captured_kwargs.update(kwargs)
            or lark_cli.LarkAuthStartResult(
                verification_url="https://open.feishu.cn/auth/mock",
                device_code="device-code",
                expires_in=600,
            )
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/integrations/lark/auth/start", json={})

    assert response.status_code == 200
    assert response.json()["verification_url"] == "https://open.feishu.cn/auth/mock"
    assert response.json()["device_code"] == "device-code"
    assert captured_kwargs == {"domains": (), "scope": None, "recommend": False}


def test_lark_auth_start_route_passes_explicit_recommend(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)
    captured_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        integrations_router,
        "start_lark_auth",
        lambda _user_id, **kwargs: (
            captured_kwargs.update(kwargs)
            or lark_cli.LarkAuthStartResult(
                verification_url="https://open.feishu.cn/auth/mock",
                device_code="device-code",
                expires_in=600,
            )
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/integrations/lark/auth/start", json={"recommend": True})

    assert response.status_code == 200
    assert response.json()["verification_url"] == "https://open.feishu.cn/auth/mock"
    assert response.json()["device_code"] == "device-code"
    assert captured_kwargs == {"domains": (), "scope": None, "recommend": True}


def test_lark_auth_complete_route_polls_device_code(monkeypatch, tmp_path):
    config = _config(tmp_path / "skills")
    app = _make_app(system_role="user", config=config)

    monkeypatch.setattr(
        integrations_router,
        "complete_lark_auth",
        lambda _user_id, _config, *, device_code: lark_cli.LarkAuthCompleteResult(
            success=True,
            message=f"completed {device_code}",
            status=lark_cli.LarkIntegrationStatus(
                installed=True,
                version="v1.0.65",
                manifest_version="v1.0.65",
                latest_available_version=None,
                runtime_version_mismatch=False,
                app_configured=True,
                app_id="cli_mock",
                app_brand="feishu",
                skills_expected=27,
                skills_installed=27,
                installed_skills=("lark-doc",),
                enabled_skills=("lark-doc",),
                install_path="/tmp/lark",
                cli=lark_cli.LarkCliProbe(available=True),
                auth=lark_cli.LarkAuthProbe(status="authenticated", user="Alice"),
            ),
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/integrations/lark/auth/complete", json={"device_code": "device-code"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"]["auth"]["status"] == "authenticated"

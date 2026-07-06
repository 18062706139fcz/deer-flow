from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import integrations as integrations_router
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli
from deerflow.skills.storage import reset_skill_storage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from deerflow.skills.types import SkillCategory


def _skill_content(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name} integration skill\n---\n\n# {name}\n"


def _make_lark_cli_source_zip(tmp_path: Path) -> Path:
    archive = tmp_path / "lark-cli.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for skill_name in lark_cli.LARK_SKILL_NAMES:
            zf.writestr(f"cli-1.0.65/skills/{skill_name}/SKILL.md", _skill_content(skill_name))
            zf.writestr(f"cli-1.0.65/skills/{skill_name}/references/readme.md", f"# {skill_name}\n")
    return archive


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
    monkeypatch.setattr(lark_cli, "probe_lark_auth", lambda _user_id: lark_cli.LarkAuthProbe(status="not_configured", message="not configured"))

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

    storage = UserScopedSkillStorage("alice", host_path=str(skills_root), app_config=config)
    skills = storage.load_skills(enabled_only=False)
    lark_doc = next(skill for skill in skills if skill.name == "lark-doc")
    assert lark_doc.category == SkillCategory.INTEGRATION
    assert lark_doc.get_container_file_path("/mnt/skills") == "/mnt/skills/integrations/lark-cli/lark-doc/SKILL.md"
    assert lark_doc.enabled is True
    reset_skill_storage()


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
        lambda _user_id, _config: lark_cli.LarkIntegrationStatus(
            installed=True,
            version="v1.0.65",
            manifest_version="v1.0.65",
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
        lambda _user_id, _config: lark_cli.LarkIntegrationStatus(
            installed=True,
            version="v1.0.65",
            manifest_version="v1.0.65",
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
        lambda _user_id, _config: lark_cli.LarkIntegrationStatus(
            installed=False,
            version="v1.0.65",
            manifest_version=None,
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

    monkeypatch.setattr(
        integrations_router,
        "start_lark_auth",
        lambda _user_id, **_kwargs: lark_cli.LarkAuthStartResult(
            verification_url="https://open.feishu.cn/auth/mock",
            device_code="device-code",
            expires_in=600,
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/integrations/lark/auth/start", json={"recommend": True})

    assert response.status_code == 200
    assert response.json()["verification_url"] == "https://open.feishu.cn/auth/mock"
    assert response.json()["device_code"] == "device-code"


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

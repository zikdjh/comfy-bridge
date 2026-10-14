"""全局健康与 Modal SDK 自检接口。"""

from app.api.v1 import health
from app.modal_volume import client as modal_cli


async def test_modal_cli_health_reports_missing_sdk(monkeypatch):
    monkeypatch.setattr(modal_cli, "sdk_available", lambda: False)
    result = await health.modal_cli_health()
    assert result["found"] is False
    assert result["path"] == "(in-process SDK)"
    assert result["error"]["category"] == "not_installed"
    assert "pip install 'modal>=1.5'" in result["error"]["next_action"]


async def test_modal_cli_health_checks_version_login_and_app(monkeypatch):
    monkeypatch.setattr(modal_cli, "sdk_available", lambda: True)
    monkeypatch.setattr(modal_cli, "sdk_version", lambda: "1.5.5")

    async def fake_app_status(app_name, *, timeout=20.0):
        assert app_name == "comfy-bridge-runner"
        return "deployed"

    monkeypatch.setattr(modal_cli, "app_status", fake_app_status)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: type("S", (), {"modal": type("M", (), {"app_name": "comfy-bridge-runner"})()})(),
    )
    result = await health.modal_cli_health()
    assert result["version"] == "modal 1.5.5"
    assert result["logged_in"] is True
    assert result["app_state"] == "deployed"
    assert result["error"] is None
    assert result["latency_ms"] is not None


async def test_modal_cli_health_maps_login_failure(monkeypatch):
    monkeypatch.setattr(modal_cli, "sdk_available", lambda: True)
    monkeypatch.setattr(modal_cli, "sdk_version", lambda: "1.5.5")

    async def fake_app_status(app_name, *, timeout=20.0):
        raise modal_cli.ModalCliError(
            "Token validation failed",
            category="not_logged_in",
        )

    monkeypatch.setattr(modal_cli, "app_status", fake_app_status)
    result = await health.modal_cli_health()
    assert result["found"] is True
    assert result["version"] == "modal 1.5.5"
    assert result["logged_in"] is False
    assert result["error"]["category"] == "not_logged_in"


async def test_modal_cli_health_maps_version_failure(monkeypatch):
    monkeypatch.setattr(modal_cli, "sdk_available", lambda: True)

    def fail_version():
        raise modal_cli.ModalCliError("SDK 缺失版本信息", category="not_installed")

    monkeypatch.setattr(modal_cli, "sdk_version", fail_version)
    result = await health.modal_cli_health()
    assert result["found"] is True
    assert result["version"] is None
    assert result["error"]["category"] == "not_installed"

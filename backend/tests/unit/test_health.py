"""全局健康与 Modal CLI 自检接口。"""

from app.api.v1 import health
from app.modal_volume import client as modal_cli


async def test_modal_cli_health_reports_missing_cli(monkeypatch):
    monkeypatch.setattr(modal_cli, "cli_path", lambda: "")
    result = await health.modal_cli_health()
    assert result["found"] is False
    assert result["error"]["category"] == "not_installed"
    assert "pip install modal" in result["error"]["next_action"]


async def test_modal_cli_health_checks_version_login_and_app(monkeypatch):
    monkeypatch.setattr(modal_cli, "cli_path", lambda: "C:/bin/modal.exe")

    async def fake_run(*args, timeout=600.0):
        if args == ("--version",):
            return "modal 1.6.0\n"
        if args == ("app", "list", "--json"):
            return '[{"description": "comfy-bridge-runner", "state": "deployed"}]'
        raise AssertionError(args)

    monkeypatch.setattr(modal_cli, "run", fake_run)
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: type("S", (), {"modal": type("M", (), {"app_name": "comfy-bridge-runner"})()})(),
    )
    result = await health.modal_cli_health()
    assert result["version"].startswith("modal ")
    assert result["logged_in"] is True
    assert result["app_state"] == "deployed"
    assert result["error"] is None
    assert result["latency_ms"] is not None


async def test_modal_cli_health_maps_login_failure(monkeypatch):
    monkeypatch.setattr(modal_cli, "cli_path", lambda: "modal")

    async def fake_run(*args, timeout=600.0):
        if args == ("--version",):
            return "modal 1.6.0"
        raise modal_cli.ModalCliError(
            "Token validation failed",
            category="not_logged_in",
        )

    monkeypatch.setattr(modal_cli, "run", fake_run)
    result = await health.modal_cli_health()
    assert result["logged_in"] is False
    assert result["error"]["category"] == "not_logged_in"

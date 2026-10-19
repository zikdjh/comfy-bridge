from __future__ import annotations

import pytest

from app.cli import DEFAULT_PORT


def test_cli_default_port_avoids_comfyui_port() -> None:
    assert DEFAULT_PORT == 18189


def test_cli_help_shows_default_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import main

    monkeypatch.setattr("sys.argv", ["comfy-bridge", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    assert "默认 18189" in capsys.readouterr().out

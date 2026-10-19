from __future__ import annotations

from starlette.requests import Request

from app.api.v1 import health
from app.core import comfy_client
from app.core.config import AppConfig, BridgeConfig
from app.runner import client as runner_client


def _request(host: str | None) -> Request:
    headers = [] if host is None else [(b"host", host.encode())]
    return Request({"type": "http", "headers": headers})


def test_bridge_entry_prefers_runtime_listen(monkeypatch) -> None:
    monkeypatch.setenv("CB_BRIDGE_RUNTIME_LISTEN", "127.0.0.1:18289")
    assert health._bridge_entry(_request("127.0.0.1:18189")) == (
        "http://127.0.0.1:18289",
        "127.0.0.1:18289",
    )


def test_bridge_entry_uses_request_host_without_runtime_override(monkeypatch) -> None:
    monkeypatch.delenv("CB_BRIDGE_RUNTIME_LISTEN", raising=False)
    assert health._bridge_entry(_request("localhost:15173")) == (
        "http://localhost:15173",
        "localhost:15173",
    )


def test_local_comfy_client_ignores_system_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    client = comfy_client._client()
    try:
        assert client.trust_env is False
    finally:
        import asyncio

        asyncio.run(client.aclose())


def test_runner_client_proxy_follows_use_system_proxy(monkeypatch) -> None:
    """云端 Runner 通道默认直连；bridge.use_system_proxy=True 时才读系统代理。"""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    settings = AppConfig(bridge=BridgeConfig(use_system_proxy=False))
    monkeypatch.setattr(runner_client, "get_settings", lambda: settings)
    client = runner_client._client()
    try:
        assert client.trust_env is False
    finally:
        import asyncio

        asyncio.run(client.aclose())

    settings = AppConfig(bridge=BridgeConfig(use_system_proxy=True))
    monkeypatch.setattr(runner_client, "get_settings", lambda: settings)
    client = runner_client._client()
    try:
        assert client.trust_env is True
    finally:
        import asyncio

        asyncio.run(client.aclose())

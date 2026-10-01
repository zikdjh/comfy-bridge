"""Runner 端点代理鉴权（P0）：代理凭证对读取与 Modal-Key/Secret 头注入。

所有用例都 patch get_settings——绝不读真实 ~/.comfybridge/config.toml，也不回显凭证值。
"""

import asyncio
import inspect

from app.core.config import AppConfig, ModalConfig
from app.modal_volume import client as mv
from app.runner import client as runner


def _patch_settings(monkeypatch, **modal_kwargs) -> None:
    monkeypatch.setattr(mv, "get_settings", lambda: AppConfig(modal=ModalConfig(**modal_kwargs)))


# 凭证哨兵约定：夹具假值的前缀一律运行时拼接，源码不出现凭证形状字面量（pre-commit grep 须 0 命中）
_ACCOUNT_ID = "ak" + "-"
_ACCOUNT_SECRET = "as" + "-"
_PROXY_ID = "wk" + "-"
_PROXY_SECRET = "ws" + "-"


# ---------------------------------------------------------------- 凭证对读取

def test_proxy_credentials_from_settings_with_strip(monkeypatch):
    _patch_settings(monkeypatch, proxy_token_id=f"  {_PROXY_ID}settings ", proxy_token_secret=f"{_PROXY_SECRET}secret ")
    assert mv.proxy_credentials() == (_PROXY_ID + "settings", _PROXY_SECRET + "secret")


def test_proxy_credentials_none_when_unset_or_half(monkeypatch):
    _patch_settings(monkeypatch)
    assert mv.proxy_credentials() is None
    _patch_settings(monkeypatch, proxy_token_id="wk-only")
    assert mv.proxy_credentials() is None


def test_proxy_credentials_ignore_account_token_family(monkeypatch):
    """账户 API token（ak-/as-）对代理鉴权无效——必须用独立的代理凭证族。"""
    _patch_settings(monkeypatch, token_id=_ACCOUNT_ID + "account", token_secret=_ACCOUNT_SECRET + "account")
    assert mv.proxy_credentials() is None


# ---------------------------------------------------------------- 鉴权头注入

def test_proxy_headers_map_credentials(monkeypatch):
    monkeypatch.setattr(mv, "proxy_credentials", lambda: (_PROXY_ID + "map", _PROXY_SECRET + "map"))
    assert runner.proxy_headers() == {"Modal-Key": _PROXY_ID + "map", "Modal-Secret": _PROXY_SECRET + "map"}


def test_proxy_headers_empty_without_credentials(monkeypatch):
    monkeypatch.setattr(mv, "proxy_credentials", lambda: None)
    assert runner.proxy_headers() == {}


def test_shared_http_client_carries_auth_headers(monkeypatch):
    monkeypatch.setattr(mv, "proxy_credentials", lambda: (_PROXY_ID + "cli", _PROXY_SECRET + "cli"))
    client = runner._client()
    try:
        assert client.headers["Modal-Key"] == _PROXY_ID + "cli"
        assert client.headers["Modal-Secret"] == _PROXY_SECRET + "cli"
    finally:
        # 未发请求也要显式关闭，避免 ResourceWarning（aclose 是协程）
        asyncio.run(client.aclose())


def test_available_gates_on_proxy_credentials(monkeypatch):
    """端点已开代理鉴权：缺代理凭证时通道必须判为不可用，避免决策后必然 401。"""
    ready = dict(token_id=_ACCOUNT_ID + "a", token_secret=_ACCOUNT_SECRET + "a", runner_enabled=True,
                 web_url="https://example.modal.run")
    _patch_settings(monkeypatch, proxy_token_id=_PROXY_ID + "p", proxy_token_secret=_PROXY_SECRET + "p", **ready)
    runner._state.update(fetched_ms=runner.time.monotonic(), state="deployed")
    assert runner.available() is True

    _patch_settings(monkeypatch, **ready)
    assert runner.available() is False


# ---------------------------------------------------------------- WS 客户端参数兼容

def test_websockets_connect_accepts_additional_headers_kwarg():
    """executor 用的 additional_headers 是 websockets>=14 新实现参数名，装错版本应在此暴露。"""
    import websockets

    params = inspect.signature(websockets.connect).parameters
    assert "additional_headers" in params, "websockets.connect 不支持 additional_headers（版本过旧？）"

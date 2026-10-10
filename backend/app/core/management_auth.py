"""管理面鉴权边界（P0）：默认本机回环，非回环监听必须配置令牌。

令牌不放进 ``config.toml`` 或配置 API：优先读 ``CB_MANAGEMENT_TOKEN``，否则读
``<config_dir>/management_token``。这样现有配置回显、日志和前端都不需要接触明文。

鉴权规则：

- 未配置令牌时保持历史行为（默认只监听 127.0.0.1，由 CLI 阻止误暴露）。
- 配置令牌后，``/api/v1``（health 除外）与 ``/bridge`` 要求 Bearer 或 Basic 凭证。
- 浏览器访问 ``/bridge`` 时由 Basic 认证弹窗接管；脚本可用
  ``Authorization: Bearer <token>``。
"""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import os
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import config_dir

_TOKEN_FILE = "management_token"


def is_loopback_host(host: str) -> bool:
    """判断监听 host 是否为回环地址；``localhost`` 与 IPv6 方括号也可识别。"""
    raw = (host or "").strip().strip("[]").lower()
    if raw in {"localhost", "localhost.localdomain"}:
        return True
    if raw == "::1":
        return True
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def management_token() -> str:
    """管理令牌：环境变量优先，其次用户配置目录下的独立文件。"""
    env = os.environ.get("CB_MANAGEMENT_TOKEN", "").strip()
    if env:
        return env
    path = config_dir() / _TOKEN_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return ""
        _, _, password = raw.partition(":")
        return password
    return request.headers.get("x-comfybridge-token", "").strip()


def _protected_path(path: str) -> bool:
    """health 保持匿名，便于容器/反向代理做就绪探测。"""
    if path == "/api/v1/health":
        return False
    return path.startswith(("/api/v1", "/bridge"))


def install_management_auth(
    app: FastAPI,
    token_getter: Callable[[], str] = management_token,
) -> None:
    """给应用安装管理面鉴权中间件；令牌为空时完全放行（本机默认模式）。"""

    @app.middleware("http")
    async def _management_auth(request: Request, call_next):
        token = token_getter().strip()
        if not token or not _protected_path(request.url.path):
            return await call_next(request)
        supplied = _extract_token(request)
        if supplied and hmac.compare_digest(supplied, token):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "管理面需要 Access Token（Bearer 或 Basic 认证）"},
            headers={"WWW-Authenticate": 'Basic realm="ComfyBridge management"'},
        )

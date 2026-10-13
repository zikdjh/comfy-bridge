"""管理面鉴权边界（P0）：默认本机回环，非回环监听必须配置令牌。

令牌不放进 ``config.toml`` 或配置 API：优先读 ``CB_MANAGEMENT_TOKEN``，否则读
``<config_dir>/management_token``。这样现有配置回显、日志和前端都不需要接触明文。

鉴权规则：

- 未配置令牌时保持历史行为（默认只监听 127.0.0.1，由 CLI 阻止误暴露）。
- 配置令牌后，``/api/v1``（health 除外）与 ``/bridge`` 要求 Bearer 或 Basic 凭证。
- 浏览器访问 ``/bridge`` 时由 Basic 认证弹窗接管；脚本可用
  ``Authorization: Bearer <token>``。
- 配置令牌后，管理面还会校验 ``Origin``：浏览器跨站发起的管理请求（CSRF）默认
  拒绝，除非其来源在 ``CB_ALLOWED_ORIGINS`` 白名单内（反向代理/独立前端域名）。
- 写操作与所有被拒请求（401/403）会写审计记录，见 ``app/core/audit.py``。
"""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import os
from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import audit
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


def _origin_of(value: str) -> str:
    """把 Origin/Referer 规整成 ``scheme://host[:port]``；无法解析返回空串。"""
    value = (value or "").strip()
    if not value or value == "null":
        return ""
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _allowed_origins() -> set[str]:
    """``CB_ALLOWED_ORIGINS``（逗号分隔）里的额外可信来源，用于反代/独立前端域名。"""
    raw = os.environ.get("CB_ALLOWED_ORIGINS", "")
    return {origin for origin in (_origin_of(item) for item in raw.split(",")) if origin}


def _same_origin(request: Request) -> bool:
    """请求的 Origin 是否与它实际访问的 Host 同源。"""
    origin = _origin_of(request.headers.get("origin", ""))
    if not origin:
        return True  # 无 Origin（脚本/同站导航）交由 token 校验决定
    host = request.headers.get("host", "")
    expected_scheme = request.url.scheme
    proxied = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if proxied:
        expected_scheme = proxied
    return origin == f"{expected_scheme.lower()}://{host.lower()}"


def _origin_allowed(request: Request) -> bool:
    """Origin 为同源、无 Origin、或在白名单内才放行（防跨站请求伪造）。"""
    origin = _origin_of(request.headers.get("origin", ""))
    if not origin:
        return True
    if origin in _allowed_origins():
        return True
    return _same_origin(request)


def _auth_scheme(request: Request) -> str:
    """审计用：只记认证方式，不记任何凭证内容。"""
    auth = request.headers.get("authorization", "").strip().lower()
    if auth.startswith("bearer "):
        return "bearer"
    if auth.startswith("basic "):
        return "basic"
    if request.headers.get("x-comfybridge-token"):
        return "header"
    return "none"


def _write_audit(request: Request, status: int, *, reason: str) -> None:
    if not audit.audit_enabled():
        return
    # 成功的只读请求不入审计，避免每次页面加载把静态资源刷进日志；
    # 写操作（改状态）与所有拒绝（401/403）必记。
    read_only = request.method in {"GET", "HEAD", "OPTIONS"}
    if read_only and status < 400:
        return
    # 优先取反代透传的真实客户端 IP，否则用直连 peer
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "")
    audit.record(
        {
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "client": client_ip,
            "origin": request.headers.get("origin", ""),
            "auth": _auth_scheme(request),
            "reason": reason,
        }
    )


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
        if not _origin_allowed(request):
            _write_audit(request, 403, reason="cross_origin")
            return JSONResponse(
                status_code=403,
                content={"detail": "跨站来源被拒绝；如需允许，请加入 CB_ALLOWED_ORIGINS"},
            )
        supplied = _extract_token(request)
        if supplied and hmac.compare_digest(supplied, token):
            response = await call_next(request)
            _write_audit(request, response.status_code, reason="ok")
            return response
        _write_audit(request, 401, reason="unauthorized")
        return JSONResponse(
            status_code=401,
            content={"detail": "管理面需要 Access Token（Bearer 或 Basic 认证）"},
            headers={"WWW-Authenticate": 'Basic realm="ComfyBridge management"'},
        )

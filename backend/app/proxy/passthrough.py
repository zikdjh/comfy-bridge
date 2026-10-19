"""透明流式透传：未接管端点原样转发本地 ComfyUI（httpx 常驻连接，流式回写，兼容 SSE/大文件）。"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from ..core.config import get_settings
from ..core.constants import HOP_BY_HOP_HEADERS
from . import cloud_overlay, privacy

_client: httpx.AsyncClient | None = None


def _rewrite_to_upstream(value: str, upstream_authority: str) -> str:
    """把 Origin/Referer 的 authority 改写为上游地址。

    ComfyUI origin_only_middleware 对回环地址校验 Host == Origin，不改写会把
    浏览器的 :18189 判成跨站返回 403（server.py §create_origin_only_middleware）。
    """
    try:
        parts = urlsplit(value)
        if not parts.netloc:
            return value
        return urlunsplit((parts.scheme, upstream_authority, parts.path, parts.query, parts.fragment))
    except ValueError:
        return value


def get_upstream_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # read=None：SSE / 长响应不超时；connect 10s 快速暴露本地不可达
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=None),
            trust_env=False,
        )
    return _client


async def close_upstream_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _upstream_url(request: Request) -> str:
    base = get_settings().local.comfy_url.rstrip("/")
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{base}{request.url.path}{query}"


def upstream_headers(request: Request) -> list[tuple[str, str]]:
    """透传用请求头：剥离逐跳头，Origin/Referer 改写为上游地址。

    与 forward_request 共用，保证任何自拼头的转发路径（如 /prompt 的
    非 JSON 兜底）都通过 ComfyUI origin_only_middleware 校验。
    """
    upstream_authority = urlsplit(get_settings().local.comfy_url).netloc
    headers: list[tuple[str, str]] = []
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP_HEADERS:
            continue
        if lk in {"origin", "referer"} and v:
            headers.append((k, _rewrite_to_upstream(v, upstream_authority)))
        else:
            headers.append((k, v))
    return headers


async def forward_request(request: Request) -> Response:
    """按原 method/headers/body 转发到本地 ComfyUI，响应流式透传。"""
    headers = upstream_headers(request)
    body = await request.body()
    client = get_upstream_client()
    upstream_req = client.build_request(request.method, _upstream_url(request), headers=headers, content=body)
    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        return StreamingResponse(
            iter([f'{{"error": "本地 ComfyUI 不可达: {exc}"}}'.encode()]),
            status_code=502,
            media_type="application/json",
        )

    # 同名重复头极少见（本地 ComfyUI 无多值响应头），dict 化即可；逐跳头已剥离
    resp_headers = {
        k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
    }

    # ComfyUI 根页面注入一次性隐私确认脚本（P2-5）；其余响应仍流式透传，
    # 不影响 SSE / 大文件。仅在开关开启且响应确为 HTML 时读取整体。
    content_type = upstream_resp.headers.get("content-type", "")
    html_inject = (
        (privacy.enabled() or cloud_overlay.enabled())
        and "text/html" in content_type.lower()
        and request.method in {"GET", "HEAD"}
        and request.url.path in {"/", "/index.html", "/api/"}
    )
    if html_inject:
        body = await upstream_resp.aread()
        await upstream_resp.aclose()
        text = body.decode("utf-8", errors="replace")
        marker = "</body>"
        script = ""
        if privacy.enabled():
            script += privacy.INJECT_SCRIPT
        if cloud_overlay.enabled():
            script += cloud_overlay.OVERLAY_SCRIPT
        if marker in text:
            text = text.replace(marker, script + marker, 1)
        else:
            text += script
        resp_headers.pop("content-length", None)
        return Response(
            content=text.encode("utf-8"),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type="text/html",
        )

    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream_resp.aclose),
    )

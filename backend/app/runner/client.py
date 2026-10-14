"""Modal 侧 ComfyUI 执行通道客户端（M1）。

- 可用性 = 凭证已配置 + runner_enabled + 应用已部署（SDK AppList，缓存 60s）
  + web_url 已配置。部署状态用后台刷新 + 缓存读取，决策路径不做阻塞 CLI 调用
  （prompt_submit 在决策前 await ensure_state() 保鲜）。
- Web URL：settings.modal.web_url（modal deploy 输出的固定端点）。
- 端点鉴权：runner web_server 开启 requires_proxy_auth，所有直连请求（HTTP 与 WS 握手）
  必须携带 Modal-Key/Modal-Secret 头（proxy_headers()），值取设置页的代理鉴权 token
  （独立凭证族 wk-…/ws-…，与账户 API token 不同）。
- 云端与本地是同一镜像同一版本 ComfyUI，/prompt /history /view /ws 协议完全一致；
  冷启动请求最长等 startup_timeout=600s，故 HTTP 读超时放宽到 660s。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..core.config import get_settings, modal_configured
from ..modal_volume import client as modal_cli

logger = logging.getLogger(__name__)

_STATE_TTL = 60.0
_state_lock = asyncio.Lock()
_state: dict = {"fetched_ms": 0.0, "state": None}  # state: deployed | stopped | ephemeral | None


def web_url() -> str | None:
    url = get_settings().modal.web_url.strip().rstrip("/")
    return url or None


def deployed_state() -> str | None:
    """最近一次探测到的应用状态（缓存值，不发起 IO）。"""
    return _state["state"] if time.monotonic() - _state["fetched_ms"] < _STATE_TTL else None


def invalidate_state() -> None:
    """账号切换后强制下轮重新探测（app_name/web_url 已随账号变化）。"""
    _state.update(fetched_ms=0.0, state=None)


async def refresh_state() -> str | None:
    """SDK AppList 探测应用部署状态（单飞）。"""
    app_name = get_settings().modal.app_name
    async with _state_lock:
        if time.monotonic() - _state["fetched_ms"] < _STATE_TTL:
            return _state["state"]
        try:
            state = await modal_cli.app_status(app_name)
        except Exception as exc:  # noqa: BLE001 —— SDK/凭证/网络失败都视为未知
            logger.warning("Modal 应用状态探测失败: %s", exc)
            state = None
        _state.update(fetched_ms=time.monotonic(), state=state)
        return state


async def ensure_state() -> str | None:
    """缓存过期才真正探测（单飞），供决策前保鲜；否则直接读缓存。"""
    if time.monotonic() - _state["fetched_ms"] < _STATE_TTL:
        return _state["state"]
    return await refresh_state()


def available() -> bool:
    """Runner 通道是否可用（缓存读，无 IO；决策路径安全）。"""
    modal = get_settings().modal
    return bool(
        modal_configured()
        and modal.runner_enabled
        and web_url()
        and modal_cli.proxy_credentials()  # 端点已开代理鉴权，缺凭证必然全部 401
        and _state["state"] == "deployed"
        and time.monotonic() - _state["fetched_ms"] < _STATE_TTL
    )


def proxy_headers() -> dict[str, str]:
    """Modal 代理鉴权头（runner web_server 开启 requires_proxy_auth 后必带）。

    头名是 Modal 边缘代理的约定（Modal-Key/Modal-Secret），值是独立的代理鉴权
    token 族（wk-…/ws-…，非账户 API token）；未配置返回空 dict，由边缘代理按
    401 拒绝，错误自然上浮到调用方。
    """
    creds = modal_cli.proxy_credentials()
    if not creds:
        return {}
    return {"Modal-Key": creds[0], "Modal-Secret": creds[1]}


def _client() -> httpx.AsyncClient:
    # 鉴权头默认注入全部云端请求；connect 15s（Modal 边缘快连）；read 覆盖冷启动 600s + 余量
    # trust_env=False：不拾取系统 HTTP(S)_PROXY，云端直连走 Modal 边缘网络。
    return httpx.AsyncClient(
        timeout=httpx.Timeout(660.0, connect=15.0),
        headers=proxy_headers(),
        trust_env=False,
    )


async def fetch_object_info() -> dict:
    """云端全量 /object_info（节点注册表）；容器未运行时 Modal 挂起请求直至冷启动完成。"""
    url = web_url()
    async with _client() as cli:
        resp = await cli.get(f"{url}/object_info")
    if resp.status_code != 200:
        raise RuntimeError(f"云端 object_info 拉取失败（HTTP {resp.status_code}）")
    return resp.json()


async def post_prompt(payload: dict) -> dict:
    """提交工作流到云端 ComfyUI，返回 {"prompt_id", "number", "node_errors"}。"""
    url = web_url()
    async with _client() as cli:
        resp = await cli.post(f"{url}/prompt", json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"云端提交被拒（HTTP {resp.status_code}）: {resp.text[:300]}")
    return resp.json()


async def post_input_file(
    filename: str, data: bytes, *, subfolder: str = "", overwrite: bool = True
) -> dict:
    """把输入文件写进运行中容器的 input/ 目录（ComfyUI `/upload/image`）。

    必须走容器内的 ComfyUI，而不是外部卷写入：Modal Volume 是快照
    语义，外部写入对**已经在运行**的容器不可见（load 时报 Invalid image file），
    而容器内写入立刻可见；文件随容器正常退出提交回卷，持久化语义不变。
    ComfyUI 该端点只落原始字节，不做图片解码校验（校验发生在 LoadImage 节点），
    因此对非图片输入同样适用。

    subfolder 为 input/ 内的相对子目录（对应工作流里的 "sub/name.png"）。
    """
    url = web_url()
    files = {"image": (filename, data, "application/octet-stream")}
    form = {"type": "input", "overwrite": "true" if overwrite else "false"}
    if subfolder:
        form["subfolder"] = subfolder
    async with _client() as cli:
        resp = await cli.post(f"{url}/upload/image", files=files, data=form)
    if resp.status_code != 200:
        raise RuntimeError(
            f"云端输入上传失败（HTTP {resp.status_code}）: {filename} {resp.text[:200]}"
        )
    return resp.json()


async def get_history(prompt_id: str) -> dict | None:
    """拉取云端 /history/<pid> 条目；未入历史返回 None。

    读超时 8s：真机实测（2026-10-03，Modal web_server × ComfyUI v0.38.0）执行期间
    /history 会挂起直到执行结束才恢复响应，30s 读超时会把轮询节奏拖垮；快速失败
    交还调用方重试。
    """
    url = web_url()
    try:
        async with _client() as cli:
            resp = await cli.get(f"{url}/history/{prompt_id}", timeout=httpx.Timeout(8.0, connect=15.0))
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return (resp.json() or {}).get(prompt_id)


async def fetch_container_logs() -> list[dict]:
    """云端容器 stdout 缓冲（/internal/logs/raw，随容器进程生命周期）。

    与本地同款端点；Modal 代理不透传 WS 帧，控制台实时性靠轮询差分。
    不可达/失败返回空列表（容器可能刚缩容）。
    """
    from ..logs.local_stream import normalize_entry

    url = web_url()
    try:
        async with _client() as cli:
            resp = await cli.get(f"{url}/internal/logs/raw", timeout=httpx.Timeout(15.0, connect=10.0))
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    return [normalize_entry(e) for e in resp.json().get("entries", [])]


async def fetch_view(filename: str, subfolder: str = "", type_: str = "output") -> bytes:
    """按 ComfyUI /view 协议下载输出文件字节。"""
    url = web_url()
    params = {"filename": filename, "subfolder": subfolder, "type": type_}
    async with _client() as cli:
        resp = await cli.get(f"{url}/view", params=params)
    if resp.status_code != 200:
        raise RuntimeError(f"云端取图失败（HTTP {resp.status_code}）: {filename}")
    return resp.content


async def post_interrupt() -> None:
    url = web_url()
    async with _client() as cli:
        try:
            await cli.post(f"{url}/interrupt", timeout=httpx.Timeout(20.0, connect=10.0))
        except httpx.HTTPError as exc:
            logger.warning("云端 /interrupt 失败: %s", exc)


def ws_url(client_id: str) -> str:
    base = web_url() or ""
    rest = base.split("://", 1)[1]
    scheme = "wss" if base.startswith("https") else "ws"
    return f"{scheme}://{rest}/ws?clientId={client_id}"

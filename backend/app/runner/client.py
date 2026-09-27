"""Modal 侧 ComfyUI 执行通道客户端（M1）。

- 可用性 = 凭证已配置 + runner_enabled + 应用已部署（`modal app list --json`，缓存 60s）
  + web_url 已配置。部署状态用后台刷新 + 缓存读取，决策路径不做阻塞 CLI 调用
  （prompt_submit 在决策前 await ensure_state() 保鲜）。
- Web URL：settings.modal.web_url（modal deploy 输出的固定端点）。
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


async def refresh_state() -> str | None:
    """`modal app list --json` 探测应用部署状态（单飞）。"""
    app_name = get_settings().modal.app_name
    async with _state_lock:
        if time.monotonic() - _state["fetched_ms"] < _STATE_TTL:
            return _state["state"]
        try:
            out = await modal_cli.run("app", "list", "--json")
            apps = modal_cli.cli_json_list(out)
            state = next((a.get("state") for a in apps if a.get("description") == app_name), None)
        except Exception as exc:  # noqa: BLE001 —— CLI 未装/凭证缺失/网络失败都视为未知
            logger.warning("modal app list 探测失败: %s", exc)
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
        and _state["state"] == "deployed"
        and time.monotonic() - _state["fetched_ms"] < _STATE_TTL
    )


def _client() -> httpx.AsyncClient:
    # connect 15s（Modal 边缘快连）；read 覆盖冷启动 600s + 余量；整体不限
    return httpx.AsyncClient(timeout=httpx.Timeout(660.0, connect=15.0))


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


async def get_history(prompt_id: str) -> dict | None:
    """拉取云端 /history/<pid> 条目；未入历史返回 None。"""
    url = web_url()
    try:
        async with _client() as cli:
            resp = await cli.get(f"{url}/history/{prompt_id}", timeout=httpx.Timeout(30.0, connect=15.0))
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
        except httpx.HTTPError as exc:  # noqa: BLE001 —— 中断尽力而为
            logger.warning("云端 /interrupt 失败: %s", exc)


def ws_url(client_id: str) -> str:
    base = web_url() or ""
    rest = base.split("://", 1)[1]
    scheme = "wss" if base.startswith("https") else "ws"
    return f"{scheme}://{rest}/ws?clientId={client_id}"

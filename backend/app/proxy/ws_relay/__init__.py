"""WS 事件中继：浏览器 ⇆ 本地 ComfyUI 双向泵，文本事件回写请求状态。

M1：新增浏览器套接字注册表（按 clientId），供 modal 执行通道把云端事件
（prompt_id 已映射回桥接 id）注入同会话的浏览器连接。
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qs

import websockets
from starlette.websockets import WebSocket

from ...core.config import get_settings
from ...logs import store

logger = logging.getLogger(__name__)

# 执行事件 → 请求状态（规划文档 §4：queued/running/done/error/cancelled）
_EVENT_STATUS = {
    "execution_start": "running",
    "execution_success": "done",
    "execution_error": "error",
    "execution_interrupted": "cancelled",
}

# —— 浏览器套接字注册表：clientId → 该会话当前打开的连接集合 ——
_sockets: dict[str, set[WebSocket]] = {}
_send_locks: dict[int, asyncio.Lock] = {}


def _client_id_of(ws: WebSocket) -> str | None:
    try:
        return parse_qs(ws.url.query).get("clientId", [None])[0]
    except Exception:  # noqa: BLE001
        return None


async def _register(ws: WebSocket, client_id: str | None) -> None:
    if not client_id:
        return
    _sockets.setdefault(client_id, set()).add(ws)
    _send_locks.setdefault(id(ws), asyncio.Lock())


async def _unregister(ws: WebSocket, client_id: str | None) -> None:
    if not client_id:
        return
    group = _sockets.get(client_id)
    if group is not None:
        group.discard(ws)
        if not group:
            _sockets.pop(client_id, None)
    _send_locks.pop(id(ws), None)


def send_event(client_id: str | None, event: dict) -> None:
    """把事件注入该 clientId 的浏览器连接（fire-and-forget；无连接则静默丢弃）。"""
    if not client_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_send_json(client_id, event))


async def _send_json(client_id: str, event: dict) -> None:
    text = json.dumps(event, ensure_ascii=False)
    for ws in list(_sockets.get(client_id, ())):
        lock = _send_locks.get(id(ws))
        try:
            if lock is not None:
                async with lock:
                    await ws.send_text(text)
            else:
                await ws.send_text(text)
        except Exception:  # noqa: BLE001 —— 单个连接失败不影响其他订阅者
            logger.debug("ws inject failed", exc_info=True)


def _upstream_ws_url() -> str:
    comfy_url = get_settings().local.comfy_url.rstrip("/")
    if "://" not in comfy_url:
        return "ws://127.0.0.1:8188/ws"  # 配置畸形时兜底，不让 /ws 直接 500
    scheme, rest = comfy_url.split("://", 1)
    return f"{'wss' if scheme == 'https' else 'ws'}://{rest}/ws"


def _tap_event(raw: str) -> None:
    """解析上游文本事件，回写我们跟踪的请求状态（异步落库，失败不影响中继）。"""
    try:
        event = json.loads(raw)
        etype = event.get("type")
        prompt_id = (event.get("data") or {}).get("prompt_id")
        if etype not in _EVENT_STATUS or not prompt_id:
            return
        error = json.dumps(event.get("data"), ensure_ascii=False)[:500] if etype == "execution_error" else None
        asyncio.get_running_loop().create_task(store.aupdate_status(prompt_id, _EVENT_STATUS[etype], error))
    except Exception:  # noqa: BLE001 —— 事件解析失败只影响状态回写，不中断中继
        logger.debug("ws event tap failed", exc_info=True)


async def relay_local_ws(browser_ws: WebSocket) -> None:
    await browser_ws.accept()
    client_id = _client_id_of(browser_ws)
    await _register(browser_ws, client_id)
    query = f"?{browser_ws.url.query}" if browser_ws.url.query else ""
    try:
        upstream = await websockets.connect(_upstream_ws_url() + query, max_size=None)
    except Exception as exc:  # noqa: BLE001 —— 上游不可达时快速关闭，前端按既定逻辑重试
        logger.warning("ws upstream connect failed: %s", exc)
        await _unregister(browser_ws, client_id)
        await browser_ws.close(code=1013)
        return

    async def browser_to_upstream() -> None:
        while True:
            message = await browser_ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("bytes") is not None:
                await upstream.send(message["bytes"])

    async def upstream_to_browser() -> None:
        async for raw in upstream:
            if isinstance(raw, str):
                _tap_event(raw)
                await browser_ws.send_text(raw)
            else:
                await browser_ws.send_bytes(raw)

    try:
        done, pending = await asyncio.wait(
            [asyncio.create_task(browser_to_upstream()), asyncio.create_task(upstream_to_browser())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()  # 单侧异常向上抛，统一在 finally 收尾
    except Exception:  # noqa: BLE001 —— 任一侧断开属正常结束
        pass
    finally:
        await _unregister(browser_ws, client_id)
        await upstream.close()
        try:
            await browser_ws.close()
        except Exception:  # noqa: BLE001
            pass

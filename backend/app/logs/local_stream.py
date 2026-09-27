"""本地 ComfyUI 实时日志采集。

数据源（ComfyUI api_server/routes/internal/internal_routes.py，官方注明 /internal/* 仅供
其前端使用、契约随版本可能变化——因此采集逻辑集中在本模块，出图/断连等语义不外泄）：

- GET   /internal/logs/raw        全量快照（stdout 拦截缓冲，deque maxlen≈300）
- PATCH /internal/logs/subscribe  按 clientId 订阅；flush 回调经 WebSocket 推送 {"type":"logs"}
- WS    /ws?clientId=...          携带 clientId 连接后即可收到订阅推送

对桥接前端的出口统一为 SSE 事件流（app/api/v1/logs.py）：
- event: snapshot  {"online": true, "entries": [...]}   连接建立 / 每次重连成功
- event: append    {"entries": [...]}                    实时增量
- event: status    {"online": bool, "error": str?}       采集通道状态变化
条目统一归一化为 {"t": iso时间, "m": 去ANSI纯文本, "level": debug|detail|info|warning|error|critical}。
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator

import httpx
import websockets

from ..core.config import get_settings

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LEVEL_RE = re.compile(r"\[(DEBUG|DETAIL|INFO|WARNING|ERROR|CRITICAL)\]")

# ws 空闲多久做一次断连检查 / 心跳注释帧（SSE 经代理时防空闲超时）
_WS_IDLE_TIMEOUT = 15.0
_RECONNECT_DELAY = 2.0
_CONNECT_TIMEOUT = 5.0


def normalize_entry(entry: dict) -> dict:
    text = _ANSI_RE.sub("", entry.get("m", "")).replace("\r", "")
    first_line = text.lstrip().split("\n", 1)[0]
    match = _LEVEL_RE.search(first_line)
    if match:
        level = match.group(1).lower()
    elif text.lstrip().startswith("!!!"):
        level = "error"
    else:
        level = "info"
    return {"t": entry.get("t", ""), "m": text, "level": level}


async def fetch_snapshot(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get("/internal/logs/raw")
    resp.raise_for_status()
    return [normalize_entry(e) for e in resp.json().get("entries", [])]


async def _subscribe(client: httpx.AsyncClient, client_id: str, enabled: bool) -> None:
    await client.patch("/internal/logs/subscribe", json={"clientId": client_id, "enabled": enabled})


async def _sleep_or_disconnected(request, delay: float) -> bool:
    await asyncio.sleep(delay)
    return await request.is_disconnected()


async def _log_events_impl(request, tail: int = 300, client_ids: list[str] | None = None) -> AsyncIterator[str]:
    """SSE 事件流：快照 → 实时增量；通道断开自动重连，重连后重新下发快照。"""

    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    settings = get_settings()
    base = settings.local.comfy_url.rstrip("/")
    ws_url = base.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/ws"
    client_id = f"comfybridge-{uuid.uuid4().hex[:12]}"
    if client_ids is not None:
        client_ids.append(client_id)  # 供外层 finally 断开时取消订阅
    tail = max(1, min(tail, 500))

    async with httpx.AsyncClient(base_url=base, timeout=_CONNECT_TIMEOUT) as client:
        while True:
            # 每轮先取快照：首次连接给历史缓冲，断线重连后也靠它补齐缺口
            try:
                snapshot = await fetch_snapshot(client)
            except Exception as exc:
                yield _sse("status", {"online": False, "error": f"无法读取本地 ComfyUI 日志：{exc}"})
                if await _sleep_or_disconnected(request, _RECONNECT_DELAY):
                    return
                continue

            yield _sse("snapshot", {"online": True, "entries": snapshot[-tail:]})

            try:
                # 先连 WS 再订阅：订阅仅登记 clientId，早于连接会有推送丢失窗口
                async with websockets.connect(f"{ws_url}?clientId={client_id}", open_timeout=_CONNECT_TIMEOUT) as ws:
                    await _subscribe(client, client_id, True)
                    yield _sse("status", {"online": True, "transport": "websocket"})
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=_WS_IDLE_TIMEOUT)
                        except asyncio.TimeoutError:
                            if await request.is_disconnected():
                                return
                            yield ": ping\n\n"
                            continue
                        if isinstance(raw, (bytes, bytearray)):
                            continue  # 预览二进制帧，与日志无关
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") != "logs":
                            continue
                        entries = [normalize_entry(e) for e in msg.get("data", {}).get("entries", [])]
                        if entries:
                            yield _sse("append", {"entries": entries})
            except Exception as exc:
                yield _sse("status", {"online": False, "error": str(exc)})

            if await _sleep_or_disconnected(request, _RECONNECT_DELAY):
                return


async def local_log_events(request, tail: int = 300) -> AsyncIterator[str]:
    """SSE 日志流：包装实现体，保证会话结束（含客户端断开）时取消 ComfyUI 侧订阅。

    ComfyUI 的 /internal/logs/subscribe 按 clientId 累积订阅者；SSE 断开若不回发
    enabled=False，重连次数越多 ComfyUI 侧订阅者越多。finally 里不允许 yield，
    但允许 await 清理（PEP 525）。
    """
    client_ids: list[str] = []
    try:
        async for chunk in _log_events_impl(request, tail, client_ids):
            yield chunk
    finally:
        for cid in client_ids:
            try:
                base = get_settings().local.comfy_url.rstrip("/")
                async with httpx.AsyncClient(base_url=base, timeout=_CONNECT_TIMEOUT) as client:
                    await _subscribe(client, cid, False)
            except Exception:  # noqa: BLE001 —— ComfyUI 已下线等场景，清理失败不抛
                pass

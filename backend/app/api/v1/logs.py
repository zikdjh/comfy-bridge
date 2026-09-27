"""日志 API：本地 ComfyUI 实时日志 + Modal Runner 执行控制台。"""

import json

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ...core.config import get_settings
from ...logs.local_stream import fetch_snapshot, local_log_events
from ...runner import client as runner_client
from ...runner import console as runner_console

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/local")
async def local_logs(tail: int = Query(default=300, ge=1, le=500)) -> dict:
    """本地 ComfyUI 日志快照（REST 兜底，实时请用 /local/stream）。"""
    base = get_settings().local.comfy_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
        try:
            entries = await fetch_snapshot(client)
            return {"online": True, "entries": entries[-tail:]}
        except Exception as exc:
            return {"online": False, "entries": [], "error": str(exc)}


@router.get("/local/stream")
async def local_log_stream(request: Request, tail: int = Query(default=300, ge=1, le=500)) -> StreamingResponse:
    """SSE：snapshot → append*（断线自动重连，重连后重发 snapshot）。"""
    return StreamingResponse(
        local_log_events(request, tail),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/modal")
async def modal_logs(tail: int = Query(default=300, ge=1, le=500)) -> dict:
    """Modal Runner 控制台快照（REST 兜底，实时请用 /modal/stream）。"""
    return {"online": runner_client.available(), "entries": runner_console.snapshot(tail)}


@router.get("/modal/stream")
async def modal_log_stream(request: Request, tail: int = Query(default=300, ge=1, le=500)) -> StreamingResponse:
    """SSE：Modal 执行器结构化事件（受理/上传/提交/进度/回传/失败），数据源在桥接器内存。

    status 事件额外携带通道可用性（available() 读缓存，无 IO），随心跳每 15s 刷新。
    """
    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def with_channel(payload: dict) -> dict:
        return {**payload,
                "channel": runner_client.available(),
                "channel_detail": runner_client.deployed_state()}

    async def gen():
        async for event, payload in runner_console.stream_entries(request, tail):
            if event == "ping":
                yield _sse("status", with_channel({"online": True}))
            elif event == "status":
                yield _sse("status", with_channel(payload))
            else:
                yield _sse(event, payload)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

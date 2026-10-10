"""统一事件流 API（P1-5）。"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ...events import stream

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(request: Request) -> StreamingResponse:
    """SSE：Last-Event-ID 重放统一事件；历史缺口时下发 resync。"""
    return StreamingResponse(
        stream(request, request.headers.get("last-event-id")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

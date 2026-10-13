"""进程内统一事件总线（P1-5）。

管理面事件源：同步任务进度、节点同步、Modal 部署、成本预算告警。
事件只保留最近一段历史；SSE 重连时按 Last-Event-ID 重放，历史缺口则发
`resync`，由前端回退到 React Query 全量刷新。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator

MAX_EVENTS = 1000
_QUEUE_MAX = 200
_IDLE_TICK = 15.0

_events: deque[dict] = deque(maxlen=MAX_EVENTS)
_subs: set[asyncio.Queue] = set()
_next_id = 0


def reset() -> None:
    """测试辅助：清空历史与订阅者，并复位单调 ID。"""
    global _next_id
    _events.clear()
    _subs.clear()
    _next_id = 0


def latest_id() -> int:
    return _next_id


def publish(event: str, data: dict) -> dict:
    """追加事件并广播；返回值是不可变副本，调用方可直接用于测试。"""
    global _next_id
    _next_id += 1
    envelope = {
        "id": _next_id,
        "event": event,
        "data": copy.deepcopy(data),
        "ts_ms": int(time.time() * 1000),
    }
    _events.append(envelope)
    for queue in list(_subs):
        try:
            queue.put_nowait(copy.deepcopy(envelope))
        except asyncio.QueueFull:
            # 慢订阅者：丢弃最旧事件并补一条 resync，避免阻塞发布方。
            try:
                queue.get_nowait()
                queue.put_nowait({"id": _next_id, "event": "resync",
                                  "data": {"latest_id": _next_id}, "ts_ms": envelope["ts_ms"]})
            except Exception:  # 单个订阅者异常不影响其他订阅者
                logging.getLogger(__name__).debug("事件订阅者队列清理失败", exc_info=True)
    return copy.deepcopy(envelope)


def snapshot() -> list[dict]:
    """返回当前历史的副本（测试/诊断用）。"""
    return [copy.deepcopy(item) for item in _events]


def replay_after(last_id: int | None) -> tuple[list[dict], bool]:
    """返回 (id > last_id 的事件, 是否发生历史缺口)。

    last_id=None 表示首次连接，不回放旧事件；前端已有查询快照。
    """
    if last_id is None:
        return [], False
    if not _events:
        return [], last_id > _next_id
    oldest = _events[0]["id"]
    return [copy.deepcopy(item) for item in _events if item["id"] > last_id], last_id < oldest - 1


def _sse(event: str, data: dict, event_id: int | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.extend([
        f"event: {event}",
        f"data: {json.dumps(data, ensure_ascii=False)}",
    ])
    return "\n".join(lines) + "\n\n"


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


async def stream(request, last_event_id: str | None = None) -> AsyncIterator[str]:
    """SSE 事件源：可选 resync → 回放 → ready（追平基线）→ 实时事件/心跳。"""
    parsed_last = _parse_last_event_id(last_event_id)
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subs.add(queue)
    try:
        replayed, gap = replay_after(parsed_last)
        if gap:
            # resync 不带 id：先让前端全量刷新，再逐条回放历史缓冲。
            yield _sse("resync", {"latest_id": _next_id})
        for item in replayed:
            yield _sse(item["event"], item["data"], item["id"])
        # ready 最后下发并携带基线 id：若回放中途断线，Last-Event-ID 指向已送达位置。
        yield _sse(
            "ready",
            {"latest_id": _next_id, "replayed": len(replayed), "gap": gap},
            _next_id,
        )

        while True:
            if await request.is_disconnected():
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_IDLE_TICK)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield _sse(item["event"], item["data"], item["id"])
    finally:
        _subs.discard(queue)

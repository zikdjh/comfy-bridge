"""Modal Runner 执行控制台（桥接器视角的云端执行事件流）。

与 local_stream（本地 ComfyUI stdout）对齐的 SSE 出口（event: snapshot/append/status）。
内容是执行器的结构化里程碑：受理 → 输入上传 → 提交（含冷启动耗时）→ 节点进度 →
输出回传 → 完成/失败/中断，按请求短 id 前缀区分并发任务。
云端容器的原始 stdout 不在此（用 `modal app logs comfyui-l40s` 查看）。

环形缓冲常驻内存（后端重启清空）；订阅者慢速消费时丢弃最旧行，不阻塞执行器。
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime

_RING: deque[dict] = deque(maxlen=500)
_SUBS: set[asyncio.Queue] = set()
_QUEUE_MAX = 200

# 空闲心跳间隔（秒）：维持 SSE 经代理不被掐断，顺带给前端刷新通道状态的机会
_IDLE_TICK = 15.0


def log(message: str, level: str = "info") -> None:
    """追加一条控制台事件并广播给所有订阅者（同步、无阻塞、永不抛出）。"""
    entry = {
        "t": datetime.now().isoformat(timespec="milliseconds"),
        "m": message,
        "level": level,
    }
    _RING.append(entry)
    for q in list(_SUBS):
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(entry)
            except Exception:  # noqa: BLE001 —— 单个慢订阅者不影响整体
                pass


def snapshot(tail: int = 300) -> list[dict]:
    return list(_RING)[-max(1, min(tail, 500)):]


async def stream_entries(request, tail: int = 300):
    """SSE 事件源：yield (event, payload)；event ∈ snapshot/append/status/ping。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _SUBS.add(q)
    try:
        # online 字段与 local 流契约一致（前端以 data.online 校验后再应用快照）
        yield "status", {"online": True, "transport": "sse"}
        yield "snapshot", {"online": True, "entries": snapshot(tail)}
        while True:
            if await request.is_disconnected():
                return
            try:
                entry = await asyncio.wait_for(q.get(), timeout=_IDLE_TICK)
                yield "append", {"entries": [entry]}
            except asyncio.TimeoutError:
                yield "ping", None
    finally:
        _SUBS.discard(q)

"""重启后对账：把遗留的云端在跑请求收敛到真实结局。

真机缺陷 LV-06（2026-10-11）：旧启动清理把 queued/running 一刀切写成 cancelled
且不带 finished_ms。云端请求重启时仍在 Modal 上跑，真实结局（成功/失败/中断）
因此被永久掩埋，成功请求的输出也不会回传。

启动清理现在放过有 modal_pid 的记录（该映射已随受理持久化到 requests 表），
由本模块在启动后轮询云端 /history 收敛：

- 有终态条目 → 按真实结局回写，成功时复用输出回传把产物补进媒体库；
- 一直查不到（云端已缩容 / 历史丢失）→ 保留 cancelled 语义但补 finished_ms，
  error 注明「云端状态未确认」。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ..logs import store
from . import client as runner_client
from .executor import _classify_entry, _download_outputs

logger = logging.getLogger(__name__)

# 对账轮询节奏：重启瞬间的容器通常还在跑，/history 只在终态后有条目。
_POLL_INTERVAL_S = 30.0
# 上限 2 小时：与 modal task_timeout 同量级；到期仍未收敛按「云端状态未确认」关单。
_TIMEOUT_S = 7200.0

_task: asyncio.Task | None = None


def start() -> None:
    """启动对账后台任务（lifespan 调用；无遗留记录时任务快速退出）。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())


async def stop() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None


async def _run() -> None:
    pending = store.pending_modal_reconcile()
    if not pending:
        return
    logger.info("重启对账：%d 条云端在跑请求待收敛", len(pending))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _TIMEOUT_S
    remaining = {item["prompt_id"]: item for item in pending}
    while remaining:
        for prompt_id, item in list(remaining.items()):
            modal_pid = item.get("modal_pid") or ""
            if not modal_pid:
                remaining.pop(prompt_id, None)
                continue
            entry = await runner_client.get_history(modal_pid)
            if entry is not None:
                await _apply(prompt_id, item, entry)
                remaining.pop(prompt_id, None)
                continue
            if loop.time() >= deadline:
                await _unconfirmed(prompt_id)
                remaining.pop(prompt_id, None)
        if remaining:
            await asyncio.sleep(_POLL_INTERVAL_S)


async def _apply(prompt_id: str, item: dict, entry: dict) -> None:
    kind, detail = _classify_entry(entry)
    if kind == "interrupted":
        await store.areconcile_finish(prompt_id, status="cancelled", started_ms=item.get("started_ms"))
        logger.info("对账 %s: 云端已中断 → cancelled", prompt_id[:8])
        return
    if kind == "error":
        await store.areconcile_finish(
            prompt_id, status="error", error=detail or "云端执行失败", started_ms=item.get("started_ms")
        )
        logger.info("对账 %s: 云端执行失败 → error", prompt_id[:8])
        return
    # success：先补回传产物再标 done（媒体库可见性与正常执行一致）
    outputs = (entry or {}).get("outputs") or {}
    try:
        count = await _download_outputs(outputs)
        await store.areconcile_finish(prompt_id, status="done", started_ms=item.get("started_ms"))
        logger.info("对账 %s: 云端成功 → done（回传 %d 个文件）", prompt_id[:8], count)
    except Exception as exc:  # 回传失败不能把成功请求标成失败
        logger.exception("对账 %s: 输出回传失败", prompt_id[:8])
        await store.areconcile_finish(prompt_id, status="done", started_ms=item.get("started_ms"))
        logger.info("对账 %s: 云端成功 → done（输出回传失败: %s）", prompt_id[:8], exc)


async def _unconfirmed(prompt_id: str) -> None:
    await store.areconcile_finish(
        prompt_id,
        status="cancelled",
        error="桥接重启，云端状态未确认（对账超时）",
        started_ms=None,
    )
    logger.warning("对账 %s: 超时未收敛 → cancelled（云端状态未确认）", prompt_id[:8])

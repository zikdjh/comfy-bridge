"""本地执行通道的终态跟踪（浏览器不在场时的兜底）。

本地请求在 /prompt 同步转发给 ComfyUI 后，请求记录原本只靠浏览器 WS 中继
（``ws_relay._tap_event``）回写状态。浏览器关闭、断线或客户端本来就是纯 HTTP
（脚本 / 集成测试）时，记录会永远停在 ``queued``：请求统计、成本台账与「请求」
页都失去终态。真机实测（2026-10-11）：纯 HTTP 提交本地文生图，请求记录一直
是 queued，而图像早已写入 output/。

本模块在受理成功后起一个轻量后台任务轮询 ComfyUI ``/history/<prompt_id>``：
该端点只在任务结束时才返回条目，因此它是权威的终态信号，与浏览器 WS 快路径
互补（共用同一状态机，终态之后的重复回写被忽略）。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..core.config import get_settings
from ..logs import store
from ..runner.executor import _classify_entry

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 2.5
_TIMEOUT_S = 3600.0  # 本地执行没有云端冷启动，1 小时足够覆盖长视频任务

# prompt_id → 跟踪任务（终态或超时后移除，防止长跑内存膨胀）
_TASKS: dict[str, asyncio.Task] = {}
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # 本地回环 + 短读超时：轮询绝不能因为一次卡住的 /history 永久停摆
        _client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def dispatch(prompt_id: str) -> bool:
    """受理本地请求的终态跟踪；同一 prompt_id 重复受理返回 False。"""
    if prompt_id in _TASKS:
        return False
    _TASKS[prompt_id] = asyncio.create_task(_run(prompt_id))
    return True


def tracked() -> int:
    """在跟踪的本地请求数（测试与自检用）。"""
    return len(_TASKS)


async def _run(prompt_id: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _TIMEOUT_S
    try:
        # 快路径：先落 running（浏览器 WS 若在场会给出同一状态，状态机幂等）
        await store.aupdate_status(prompt_id, "running")
        while True:
            entry = await _history(prompt_id)
            if entry is not None:
                kind, detail = _classify_entry(entry)
                if kind == "interrupted":
                    await store.aupdate_status(prompt_id, "cancelled")
                elif kind == "error":
                    await store.aupdate_status(prompt_id, "error", detail or "本地执行失败")
                else:
                    await store.aupdate_status(prompt_id, "done")
                return
            if loop.time() >= deadline:
                logger.warning("本地请求 %s 跟踪超时（>%.0fs），保留当前状态", prompt_id[:8], _TIMEOUT_S)
                return
            await asyncio.sleep(_POLL_INTERVAL_S)
    except asyncio.CancelledError:
        raise
    except Exception:  # 跟踪失败不影响已提交的执行
        logger.exception("本地请求 %s 终态跟踪异常", prompt_id[:8])
    finally:
        _TASKS.pop(prompt_id, None)


async def _history(prompt_id: str) -> dict | None:
    """ComfyUI /history/<pid>；未结束返回 None（该端点只在任务结束时出现条目）。"""
    url = get_settings().local.comfy_url.rstrip("/")
    try:
        resp = await _http().get(f"{url}/history/{prompt_id}")
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        entry = (resp.json() or {}).get(prompt_id)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) and entry else None

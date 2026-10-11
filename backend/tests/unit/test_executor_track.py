"""云端跟踪循环：WS 泵阻塞 HTTP 时必须靠周期性收泵探测收敛。

真机回归（2026-10-11）：中断在云端 0.02s 就结束，但 ComfyUI 未广播终态事件，
泵一直挂着 → /history 永远读超时 → 请求挂到 task_timeout(3600s)。实测：断开
桥接器的 WS 后，同一条 /history 立刻 200 返回中断条目。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.runner import executor

_real_sleep = asyncio.sleep


async def _yield_once(_seconds: float) -> None:
    """把循环里的 2.5s 等待压缩成一次调度让出，让泵任务真有机会启动。"""
    await _real_sleep(0)


def _entry(messages: list) -> dict:
    return {
        "prompt": ["1", "m1", {}, {}, []],
        "outputs": {},
        "status": {"status_str": "error", "completed": False, "messages": messages},
    }


async def test_probe_suspends_pump_and_converges(monkeypatch) -> None:
    cancelled = {"n": 0}
    history = {"n": 0}
    # 模拟真机：泵存活期间 /history 永远读超时（None）；只有收泵后云端才报得出中断
    cloud_reports_interrupt = {"done": False}

    async def never_ending_pump(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["n"] += 1
            cloud_reports_interrupt["done"] = True
            raise

    async def fake_history(_pid: str):
        history["n"] += 1
        if not cloud_reports_interrupt["done"]:
            return None
        return _entry([["execution_interrupted", {"node_id": 3}]])

    async def no_logs() -> list:
        return []

    monkeypatch.setattr(executor, "_ws_pump", never_ending_pump)
    monkeypatch.setattr(executor.runner_client, "get_history", fake_history)
    monkeypatch.setattr(executor.runner_client, "fetch_container_logs", no_logs)
    monkeypatch.setattr(executor, "_WS_PROBE_INTERVAL_S", 0.01)
    monkeypatch.setattr(executor, "_PUMP_RESUME_DELAY_S", 0.0)
    monkeypatch.setattr(
        executor.config,
        "get_settings",
        lambda: SimpleNamespace(modal=SimpleNamespace(task_timeout=3600.0)),
    )
    monkeypatch.setattr(executor.asyncio, "sleep", _yield_once)

    kind, entry, _detail = await asyncio.wait_for(
        executor._track("p-track", {"client_id": "c1", "modal_pid": "m1"}),
        timeout=5,
    )
    assert kind == "interrupted"
    assert entry is not None
    # 收敛的前提就是「主动收泵」这一拍——没有它 /history 永远拿不到条目
    assert cancelled["n"] >= 1
    assert history["n"] >= 2


async def test_no_client_id_skips_pump(monkeypatch) -> None:
    started: list = []

    async def fake_pump(*args, **kwargs):
        started.append(1)
        await asyncio.Event().wait()

    async def fake_history(_pid: str):
        return _entry([])

    async def no_logs() -> list:
        return []

    monkeypatch.setattr(executor, "_ws_pump", fake_pump)
    monkeypatch.setattr(executor.runner_client, "get_history", fake_history)
    monkeypatch.setattr(executor.runner_client, "fetch_container_logs", no_logs)
    monkeypatch.setattr(
        executor.config,
        "get_settings",
        lambda: SimpleNamespace(modal=SimpleNamespace(task_timeout=3600.0)),
    )

    kind, entry, _detail = await executor._track("p-noclient", {"modal_pid": "m2"})
    assert kind == "error"
    assert entry is not None and started == []


async def test_interrupt_suspends_pump_before_posting(monkeypatch) -> None:
    """中断必须先收泵再发 /interrupt：泵存活时中断请求会排队读超时、根本没送达。"""
    order: list[str] = []

    async def pump_body() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            order.append("cancel")
            raise

    async def fake_post() -> None:
        order.append("post")

    pump = asyncio.create_task(pump_body())
    await _yield_once(0)  # 让泵真正跑起来，取消才有意义
    state = {"pump": pump, "pump_pause_until": 0.0}
    monkeypatch.setattr(executor.runner_client, "post_interrupt", fake_post)

    await executor._interrupt_task(state)

    assert order == ["cancel", "post"]
    assert state["pump"] is None
    assert state["pump_pause_until"] > 0

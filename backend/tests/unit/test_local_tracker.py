"""本地请求终态跟踪：没有浏览器 WS 也要从 /history 收敛到终态。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.logs import store
from app.proxy import local_tracker


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store._conn = None
    store.init_db()
    local_tracker._TASKS.clear()
    yield store
    local_tracker._TASKS.clear()
    store.close_db()


def _insert(pid: str, status: str = "queued") -> str:
    store.insert_request({
        "prompt_id": pid,
        "created_at": store.now_iso(),
        "created_ms": store.now_ms(),
        "backend": "local",
        "final_backend": "local",
        "gen_type": "image",
        "workflow_name": None,
        "model_name": None,
        "graph_hash": None,
        "graph_file": None,
        "client_id": None,
        "status": status,
        "rule_trace": None,
        "error": None,
    })
    return pid


def _entry(status_str: str, messages: list | None = None) -> dict:
    return {
        "prompt": ["1", "pid", {}, {}, []],
        "outputs": {},
        "status": {
            "status_str": status_str,
            "completed": status_str == "success",
            "messages": messages or [],
        },
    }


def _row(pid: str) -> dict:
    item = store.get_request_by_prompt(pid)
    assert item is not None
    return item


async def _run_once(pid: str) -> None:
    task = local_tracker._TASKS[pid]
    await asyncio.wait_for(task, timeout=5)


async def test_done_on_success(monkeypatch, tmp_db) -> None:
    pid = _insert("p-done")

    async def fake_history(_pid: str):
        return _entry("success")

    monkeypatch.setattr(local_tracker, "_history", fake_history)
    assert local_tracker.dispatch(pid) is True
    await _run_once(pid)
    rec = _row(pid)
    assert rec["status"] == "done"
    assert rec["started_ms"] and rec["finished_ms"]
    assert local_tracker.tracked() == 0


async def test_error_carries_detail(monkeypatch, tmp_db) -> None:
    pid = _insert("p-err")
    messages = [["execution_error", {"exception_message": "CUDA out of memory"}]]

    async def fake_history(_pid: str):
        return _entry("error", messages)

    monkeypatch.setattr(local_tracker, "_history", fake_history)
    local_tracker.dispatch(pid)
    await _run_once(pid)
    rec = _row(pid)
    assert rec["status"] == "error"
    assert "CUDA out of memory" in (rec["error"] or "")


async def test_interrupt_maps_to_cancelled(monkeypatch, tmp_db) -> None:
    pid = _insert("p-int")
    messages = [["execution_interrupted", {"node_id": 3}]]

    async def fake_history(_pid: str):
        return _entry("error", messages)

    monkeypatch.setattr(local_tracker, "_history", fake_history)
    local_tracker.dispatch(pid)
    await _run_once(pid)
    assert _row(pid)["status"] == "cancelled"


async def test_polls_until_entry_appears(monkeypatch, tmp_db) -> None:
    pid = _insert("p-late")
    calls = {"n": 0}

    async def fake_history(_pid: str):
        calls["n"] += 1
        return None if calls["n"] < 3 else _entry("success")

    monkeypatch.setattr(local_tracker, "_history", fake_history)
    monkeypatch.setattr(local_tracker, "_POLL_INTERVAL_S", 0.01)
    local_tracker.dispatch(pid)
    await _run_once(pid)
    assert calls["n"] == 3
    assert _row(pid)["status"] == "done"


async def test_duplicate_dispatch_rejected(monkeypatch, tmp_db) -> None:
    pid = _insert("p-dup")
    gate = asyncio.Event()

    async def fake_history(_pid: str):
        await gate.wait()
        return _entry("success")

    monkeypatch.setattr(local_tracker, "_history", fake_history)
    assert local_tracker.dispatch(pid) is True
    assert local_tracker.dispatch(pid) is False
    gate.set()
    await _run_once(pid)


async def test_timeout_keeps_status_and_cleans_registry(monkeypatch, tmp_db) -> None:
    pid = _insert("p-timeout")

    async def never(_pid: str):
        return None

    monkeypatch.setattr(local_tracker, "_history", never)
    monkeypatch.setattr(local_tracker, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(local_tracker, "_TIMEOUT_S", 0.05)
    local_tracker.dispatch(pid)
    await _run_once(pid)
    assert _row(pid)["status"] == "running"
    assert local_tracker.tracked() == 0


async def test_history_parses_entry(monkeypatch) -> None:
    pid = "p-http"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={pid: _entry("success")})
    )
    monkeypatch.setattr(
        local_tracker, "_client", httpx.AsyncClient(transport=transport)
    )
    entry = await local_tracker._history(pid)
    assert entry is not None and entry["status"]["status_str"] == "success"
    await local_tracker.aclose()


async def test_history_returns_none_while_running(monkeypatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    monkeypatch.setattr(
        local_tracker, "_client", httpx.AsyncClient(transport=transport)
    )
    assert await local_tracker._history("p-running") is None
    await local_tracker.aclose()

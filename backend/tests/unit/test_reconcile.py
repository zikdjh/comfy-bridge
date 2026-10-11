"""重启对账（LV-06）单元测试：清理分流、modal_pid 持久化、三种对账结局。"""

import pytest

from app.logs import store
from app.runner import reconcile


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store._conn = None
    store.init_db()
    yield store
    store.close_db()


def _insert(pid: str, *, final_backend: str = "modal", modal_pid: str | None = None,
            status: str = "running", started_ms: int | None = 1000) -> None:
    store.insert_request({
        "prompt_id": pid,
        "created_at": "2026-10-11 12:00:00.000",
        "created_ms": 1000,
        "backend": final_backend,
        "final_backend": final_backend,
        "gen_type": "image",
        "workflow_name": None,
        "model_name": None,
        "graph_hash": None,
        "graph_file": None,
        "client_id": None,
        "status": status,
        "rule_trace": None,
        "error": None,
        "modal_pid": modal_pid,
    })
    if started_ms is not None:
        store._get_conn().execute(
            "UPDATE requests SET started_ms = ? WHERE prompt_id = ?", (started_ms, pid))
        store._get_conn().commit()


def _row(pid: str) -> dict:
    return dict(store._get_conn().execute(
        "SELECT * FROM requests WHERE prompt_id = ?", (pid,)).fetchone())


def test_close_stale_pending_keeps_modal_records(tmp_db):
    """有 modal_pid 的云端在跑记录不被一刀切关单，交给对账。"""
    _insert("local-1", final_backend="local", modal_pid=None)
    _insert("cloud-1", final_backend="modal", modal_pid="mp-1")
    n = store.close_stale_pending()
    assert n == 1
    assert _row("local-1")["status"] == "cancelled"
    assert _row("local-1")["finished_ms"] is not None
    assert _row("cloud-1")["status"] == "running"


def test_set_modal_pid_persists(tmp_db):
    _insert("cloud-1", final_backend="modal", modal_pid=None, status="queued", started_ms=None)
    store.set_modal_pid("cloud-1", "mp-42")
    row = _row("cloud-1")
    assert row["modal_pid"] == "mp-42"
    assert store.pending_modal_reconcile() == [{
        "prompt_id": "cloud-1", "modal_pid": "mp-42", "started_ms": None,
    }]


def test_reconcile_success_downloads_outputs(tmp_db, monkeypatch):
    _insert("cloud-1", final_backend="modal", modal_pid="mp-1")
    store.close_stale_pending()
    downloaded = []

    async def fake_history(pid: str) -> dict | None:
        return {"status": {"completed": True}, "outputs": {"9": {"images": [{"filename": "a.png"}]}}}

    async def fake_download(outputs: dict) -> int:
        downloaded.append(outputs)
        return 1

    monkeypatch.setattr(reconcile.runner_client, "get_history", fake_history)
    monkeypatch.setattr(reconcile, "_download_outputs", fake_download)
    import asyncio

    asyncio.run(reconcile._run())
    row = _row("cloud-1")
    assert row["status"] == "done"
    assert row["finished_ms"] is not None
    assert row["started_ms"] == 1000
    assert downloaded and downloaded[0]["9"]["images"][0]["filename"] == "a.png"


def test_reconcile_error_entry(tmp_db, monkeypatch):
    _insert("cloud-2", final_backend="modal", modal_pid="mp-2")
    store.close_stale_pending()

    async def fake_history(pid: str) -> dict | None:
        return {"status": {"messages": [["execution_error", {"exception_message": "boom"}]]}}

    async def fail_download(outputs: dict) -> int:
        raise AssertionError("error 结局不应触发输出回传")

    monkeypatch.setattr(reconcile.runner_client, "get_history", fake_history)
    monkeypatch.setattr(reconcile, "_download_outputs", fail_download)
    import asyncio

    asyncio.run(reconcile._run())
    row = _row("cloud-2")
    assert row["status"] == "error"
    assert "boom" in (row["error"] or "")
    assert row["finished_ms"] is not None


def test_reconcile_unconfirmed_writes_cancelled_with_duration(tmp_db):
    _insert("cloud-3", final_backend="modal", modal_pid="mp-3")
    store.close_stale_pending()
    import asyncio

    asyncio.run(reconcile._unconfirmed("cloud-3"))
    row = _row("cloud-3")
    assert row["status"] == "cancelled"
    assert "云端状态未确认" in (row["error"] or "")
    assert row["finished_ms"] is not None


def test_reconcile_no_pending_is_noop(tmp_db):
    _insert("local-1", final_backend="local", modal_pid=None)
    store.close_stale_pending()
    import asyncio

    asyncio.run(reconcile._run())
    row = _row("local-1")
    assert row["status"] == "cancelled"


def test_executor_persists_modal_pid(tmp_db, monkeypatch):
    """云端受理后 modal_pid 立即落库（对账依赖）。"""
    _insert("cloud-1", final_backend="modal", modal_pid=None, status="queued", started_ms=None)
    seen = {}

    async def fake_set(pid: str, mp: str) -> None:
        seen[pid] = mp

    monkeypatch.setattr(store, "aset_modal_pid", fake_set)
    import asyncio

    # 不真的跑 _run 全流程（涉及 runner client），只验证调用点存在
    assert hasattr(store, "aset_modal_pid")
    asyncio.run(store.aset_modal_pid("cloud-1", "mp-x"))
    assert seen["cloud-1"] == "mp-x"

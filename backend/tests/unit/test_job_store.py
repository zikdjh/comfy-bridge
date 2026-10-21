"""作业状态存储接缝（P2-2）：默认内存实现的语义与业务模块接入。"""

from __future__ import annotations

import asyncio
import copy
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.core import job_store
from app.sync import jobs as sync_jobs


@pytest.fixture()
def tmp_path():
    """Windows sandbox 对新建目录有删除限制；每个用例用独立单文件 SQLite。"""
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".job-test-{uuid.uuid4().hex}.sqlite3"

    class _Dir:
        def __truediv__(self, name: str) -> Path:
            if name != "jobs.sqlite3":
                raise ValueError(f"unexpected test file: {name}")
            return path

    try:
        yield _Dir()
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


class _CopyingStore(job_store.InMemoryJobStore):
    """按值保存，模拟 Redis/PG 等外部实现，用于验证业务代码不会漏 ``save``。"""

    def __init__(self) -> None:
        super().__init__()
        self._snapshots: dict[str, dict[str, dict]] = {}

    def create(self, namespace: str, job_id: str, job):
        # 外部实现可缓存本地工作副本；这里同时保存一份快照用于断言。
        self._snapshots.setdefault(namespace, {})[job_id] = copy.deepcopy(job)
        return super().create(namespace, job_id, job)

    def save(self, namespace: str, job_id: str, job) -> None:
        self._snapshots.setdefault(namespace, {})[job_id] = copy.deepcopy(job)
        super().save(namespace, job_id, job)

    def snapshot(self, namespace: str, job_id: str):
        return self._snapshots.get(namespace, {}).get(job_id)


def test_in_memory_create_get_list_delete_clear():
    store = job_store.InMemoryJobStore()
    store.create("ns", "a", {"id": "a", "state": "running", "created_ms": 1})
    store.create("ns", "b", {"id": "b", "state": "ok", "created_ms": 2})

    assert store.get("ns", "a") == {"id": "a", "state": "running", "created_ms": 1}
    assert [item["id"] for item in store.list("ns")] == ["a", "b"]
    assert store.get("other", "a") is None

    store.delete("ns", "a")
    assert store.get("ns", "a") is None
    store.clear("ns")
    assert store.list("ns") == []


def test_prune_keeps_running_and_removes_oldest_finished():
    store = job_store.InMemoryJobStore()
    for job_id, state, created in [
        ("running-old", "running", 1),
        ("done-old", "ok", 2),
        ("done-mid", "ok", 3),
        ("done-new", "ok", 4),
    ]:
        store.create("ns", job_id, {"id": job_id, "state": state, "created_ms": created})

    removed = store.prune(
        "ns",
        3,
        is_running=lambda job: job["state"] == "running",
        created_at=lambda job: job["created_ms"],
    )

    assert removed == ["done-old"]
    assert sorted(item["id"] for item in store.list("ns")) == [
        "done-mid",
        "done-new",
        "running-old",
    ]


def test_prune_never_removes_running_when_over_capacity():
    store = job_store.InMemoryJobStore()
    store.create("ns", "r1", {"state": "running", "created_ms": 1})
    store.create("ns", "r2", {"state": "running", "created_ms": 2})

    removed = store.prune(
        "ns",
        0,
        is_running=lambda job: job["state"] == "running",
        created_at=lambda job: job["created_ms"],
    )

    assert removed == []
    assert len(store.list("ns")) == 2


def test_prune_finished_only_capacity_mode():
    store = job_store.InMemoryJobStore()
    store.create("ns", "running", {"state": "running", "created_ms": 1})
    for index in range(2, 6):
        store.create("ns", f"done-{index}", {"state": "ok", "created_ms": index})

    removed = store.prune(
        "ns",
        2,
        is_running=lambda job: job["state"] == "running",
        created_at=lambda job: job["created_ms"],
        include_running=False,
    )

    assert removed == ["done-2", "done-3"]
    assert store.get("ns", "running") is not None


def test_set_default_store_isolates_and_restores():
    original = job_store.set_default_store(job_store.InMemoryJobStore())
    try:
        job_store.default_store().create("ns", "x", {"id": "x"})
        assert job_store.default_store().get("ns", "x") == {"id": "x"}
    finally:
        job_store.set_default_store(original)
        assert job_store.default_store() is original


async def test_sync_jobs_use_store_and_keep_lifecycle():
    sync_jobs.reset()

    async def runner(handle):
        handle.log("开始")
        handle.advance({"name": "a", "ok": True})

    job = sync_jobs.start_job("node_sync", "节点同步", 1, runner)
    assert job["state"] == "running"
    stored = job_store.default_store().get("sync", job["id"])
    assert stored is not None and stored["label"] == "节点同步"

    done = await sync_jobs.wait_job(job["id"])
    assert done is not None and done["state"] == "ok"
    # 完成后必须有终态持久化调用，缓存实现里也应反映 finished_ms。
    persisted = job_store.default_store().get("sync", job["id"])
    assert persisted["finished_ms"] is not None


def test_sync_jobs_reset_clears_namespace():
    async def scenario():
        async def runner(handle):
            handle.advance({"name": "a", "ok": True})

        sync_jobs.start_job("node_sync", "x", 1, runner)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert job_store.default_store().list("sync")
    sync_jobs.reset()
    assert job_store.default_store().list("sync") == []


async def test_sync_jobs_persist_every_progress_via_store():
    previous = job_store.set_default_store(_CopyingStore())
    sync_jobs.reset()
    try:
        async def runner(handle):
            handle.log("开始")
            handle.advance({"name": "a", "ok": True})

        job = sync_jobs.start_job("node_sync", "节点同步", 1, runner)
        done = await sync_jobs.wait_job(job["id"])
        assert done is not None and done["state"] == "ok"

        persisted = job_store.default_store().snapshot("sync", job["id"])
        assert persisted is not None
        assert persisted["done"] == 1
        assert persisted["items"] == [{"name": "a", "ok": True}]
        assert any("开始" in line for line in persisted["logs"])
        assert persisted["finished_ms"] is not None
    finally:
        sync_jobs.reset()
        job_store.set_default_store(previous)


def test_sqlite_job_store_survives_reopen_and_prunes_finished(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = job_store.SQLiteJobStore(str(db))
    store.create("sync", "a", {"id": "a", "state": "running", "created_ms": 1})
    store.create("sync", "b", {"id": "b", "state": "ok", "created_ms": 2})
    store.close()

    reopened = job_store.SQLiteJobStore(str(db))
    try:
        assert [job["id"] for job in reopened.list("sync")] == ["a", "b"]
        removed = reopened.prune(
            "sync",
            1,
            is_running=lambda job: job["state"] == "running",
            created_at=lambda job: job["created_ms"],
        )
        assert removed == ["b"]
        assert reopened.get("sync", "a")["state"] == "running"
    finally:
        reopened.close()


def test_sqlite_job_store_terminal_snapshot_cannot_be_downgraded(tmp_path):
    store = job_store.SQLiteJobStore(
        str(tmp_path / "jobs.sqlite3"),
        is_terminal=lambda job: job.get("state") in {"ok", "error", "interrupted"},
    )
    try:
        running = {"id": "a", "state": "running", "created_ms": 1, "done": 1}
        store.create("sync", "a", running)
        stale = copy.deepcopy(running)
        finished = {**running, "state": "interrupted", "finished_ms": 10}
        store.save("sync", "a", finished)

        saved = store.save("sync", "a", stale)

        assert saved["state"] == "interrupted"
        assert store.get("sync", "a")["finished_ms"] == 10
    finally:
        store.close()


def test_sqlite_job_store_does_not_overwrite_interrupted_with_stale_running(tmp_path):
    store = job_store.SQLiteJobStore(str(tmp_path / "jobs.sqlite3"))
    store.set_terminal_predicate(lambda job: job.get("state") == "interrupted")
    try:
        running = {"id": "a", "state": "running", "created_ms": 1}
        store.create("sync", "a", running)
        store.save("sync", "a", {**running, "state": "interrupted", "finished_ms": 2})

        stale = store.save("sync", "a", {**running, "done": 1})

        assert stale["state"] == "interrupted"
        assert stale["finished_ms"] == 2
        assert "done" not in store.get("sync", "a")
    finally:
        store.close()


def test_sqlite_job_store_terminal_predicates_are_namespace_scoped(tmp_path):
    store = job_store.SQLiteJobStore(str(tmp_path / "jobs.sqlite3"))
    store.set_terminal_predicate(
        lambda job: job.get("state") in {"done", "interrupted"},
        namespace="deploy",
    )
    store.set_terminal_predicate(
        lambda job: job.get("state") == "done",
        namespace="scan",
    )
    try:
        deploy_running = {"id": "a", "state": "running", "created_ms": 1}
        scan_running = {"id": "b", "state": "running", "created_ms": 2}
        store.create("deploy", "a", deploy_running)
        store.create("scan", "b", scan_running)
        store.save("deploy", "a", {**deploy_running, "state": "done"})

        # 部署终态不应把扫描命名空间中的同名通用状态也锁死。
        progressed = store.save("scan", "b", {**scan_running, "scanned_dirs": 10})
        assert progressed["state"] == "running"
        assert progressed["scanned_dirs"] == 10

        stale = store.save("deploy", "a", {**deploy_running, "log_tail": ["late"]})
        assert stale["state"] == "done"
        assert "log_tail" not in stale
    finally:
        store.close()


def test_sqlite_job_store_does_not_create_second_schema_connection(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = job_store.SQLiteJobStore(str(db))
    try:
        store.create("sync", "a", {"id": "a", "state": "running", "created_ms": 1})
        store.save("sync", "a", {"id": "a", "state": "ok", "created_ms": 1})
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        store.close()


def test_recover_interrupted_only_marks_running_and_is_idempotent():
    previous = job_store.set_default_store(job_store.InMemoryJobStore())
    try:
        store = job_store.default_store()
        store.create("sync", "running", {
            "id": "running", "kind": "node_sync", "state": "running",
            "created_ms": 1, "items": [{"name": "done", "ok": True}],
        })
        store.create("sync", "done", {
            "id": "done", "kind": "workflow_sync", "state": "ok",
            "created_ms": 2, "finished_ms": 2,
        })

        assert sync_jobs.recover_interrupted() == 1
        running = sync_jobs.get_job("running")
        done = sync_jobs.get_job("done")
        assert running["state"] == "interrupted"
        assert running["items"] == [{"name": "done", "ok": True}]
        assert running["finished_ms"] is not None
        assert done["state"] == "ok"
        assert done["finished_ms"] == 2
        assert sync_jobs.recover_interrupted() == 0
    finally:
        job_store.set_default_store(previous)


async def test_sync_job_restart_history_visible_after_reopen(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    previous = job_store.set_default_store(job_store.SQLiteJobStore(str(db)))
    sync_jobs.reset()
    try:
        async def runner(handle):
            handle.advance({"name": "a", "ok": True})

        job = sync_jobs.start_job("node_sync", "重启前", 1, runner)
        done = await sync_jobs.wait_job(job["id"])
        assert done["state"] == "ok"
    finally:
        job_id = job["id"]
        job_store.close_default()
        job_store.set_default_store(previous)

    reopened = job_store.SQLiteJobStore(str(db))
    try:
        row = reopened.get("sync", job_id)
        assert row["state"] == "ok"
        assert row["items"] == [{"name": "a", "ok": True}]
        assert row["finished_ms"] is not None
    finally:
        reopened.close()


async def test_stale_worker_snapshot_does_not_overwrite_interrupted_job(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    sqlite = job_store.SQLiteJobStore(
        str(db),
        is_terminal=lambda job: job.get("state") in {"ok", "error", "cancelled", "interrupted"},
    )
    previous = job_store.set_default_store(sqlite)
    sync_jobs.reset()
    entered = asyncio.Event()
    release = asyncio.Event()
    try:
        async def runner(handle):
            entered.set()
            await release.wait()
            handle.advance({"name": "late", "ok": True})

        job = sync_jobs.start_job("node_sync", "崩溃模拟", 1, runner)
        await entered.wait()
        assert sync_jobs.recover_interrupted() == 1
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        row = sync_jobs.get_job(job["id"])
        assert row["state"] == "interrupted"
        assert row["finished_ms"] is not None
        # 视图会补一条“未知未完成项”，原始快照里不能出现晚到条目。
        raw = job_store.default_store().get("sync", job["id"])
        assert raw["items"] == []
    finally:
        job_store.close_default()
        job_store.set_default_store(previous)


async def test_restart_recovery_does_not_replay_completed_items():
    previous = job_store.set_default_store(job_store.InMemoryJobStore())
    sync_jobs.reset()
    calls: list[str] = []
    try:
        store = job_store.default_store()
        store.create("sync", "old", {
            "id": "old", "kind": "workflow_sync", "label": "历史", "state": "running",
            "phase": "上传", "total": 2, "done": 1,
            "items": [{"name": "a", "ok": True}],
            "logs": [], "error": None, "cancellable": True,
            "cancel_requested": False, "created_ms": 1,
            "started_ms": None, "finished_ms": None,
        })

        def fake_start(*_a, **_k):
            calls.append("start")
            raise AssertionError("重启恢复不应自动重放")

        original = sync_jobs.start_job
        sync_jobs.start_job = fake_start
        try:
            sync_jobs.recover_interrupted()
        finally:
            sync_jobs.start_job = original
        assert calls == []
        assert sync_jobs.get_job("old")["done"] == 1
    finally:
        sync_jobs.reset()
        job_store.set_default_store(previous)

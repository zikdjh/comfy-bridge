"""作业状态存储接缝（P2-2）：默认内存实现的语义与业务模块接入。"""

from __future__ import annotations

import asyncio
import copy

from app.core import job_store
from app.sync import jobs as sync_jobs


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

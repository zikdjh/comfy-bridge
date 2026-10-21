"""部署与 ComfyUI 扫描任务的持久化、重启中断和终态墓碑。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.core import comfy_scan, job_store
from app.nodes_mgr import deploy


def _workspace_db() -> tuple[Path, str]:
    root = (
        Path(__file__).resolve().parents[2]
        / "data"
        / f"cb-task-state-{uuid.uuid4().hex[:10]}"
    )
    root.mkdir(parents=True)
    return root, str(root / "jobs.sqlite3")


def _deploy_job(job_id: str) -> dict:
    return {
        "id": job_id,
        "state": "running",
        "created_ms": 1000,
        "started_ms": 1000,
        "finished_ms": None,
        "label": "回归部署",
        "error": None,
        "log_tail": ["building"],
    }


def _scan_job(job_id: str) -> dict:
    return {
        "id": job_id,
        "status": "running",
        "created_ms": 2000,
        "started_ms": 2000,
        "finished_ms": None,
        "drives": ["D:\\"],
        "excluded": ["C:"],
        "current_drive": "D:\\",
        "scanned_dirs": 321,
        "found": [],
        "truncated": False,
        "cancelled": False,
        "error": None,
    }


def test_deploy_interruption_survives_reopen_and_terminal_is_immutable() -> None:
    root, db_path = _workspace_db()
    store = job_store.SQLiteJobStore(db_path)
    previous = job_store.set_default_store(store)
    try:
        deploy.reset()
        store.create(deploy._NAMESPACE, "deploy-running", _deploy_job("deploy-running"))

        assert deploy.recover_interrupted() == 1
        raw = store.get(deploy._NAMESPACE, "deploy-running")
        assert raw is not None
        assert raw["state"] == "interrupted"
        assert "不要重复部署" in raw["error"]
        assert "asyncio" not in raw and "proc" not in raw
        assert deploy.recover_interrupted() == 0

        store.close()
        reopened = job_store.SQLiteJobStore(db_path)
        job_store.set_default_store(reopened)
        deploy._JOB_ID = None
        deploy._TASK = None

        restored = deploy.job_state()
        assert restored["id"] == "deploy-running"
        assert restored["state"] == "interrupted"

        stale = dict(restored, state="ok", error=None, log_tail=["late success"])
        canonical = deploy._save_job(stale)
        assert canonical["state"] == "interrupted"
        assert reopen_raw(reopened)["state"] == "interrupted"
    finally:
        current = job_store.default_store()
        if isinstance(current, job_store.SQLiteJobStore):
            current.close()
        job_store.set_default_store(previous)
        shutil.rmtree(root, ignore_errors=True)


def reopen_raw(store: job_store.SQLiteJobStore) -> dict:
    raw = store.get(deploy._NAMESPACE, "deploy-running")
    assert raw is not None
    return raw


def test_scan_interruption_survives_reopen_and_is_idempotent() -> None:
    root, db_path = _workspace_db()
    store = job_store.SQLiteJobStore(db_path)
    previous = job_store.set_default_store(store)
    try:
        comfy_scan.reset()
        store.create(comfy_scan._NAMESPACE, "scan-running", _scan_job("scan-running"))

        assert comfy_scan.recover_interrupted() == 1
        raw = store.get(comfy_scan._NAMESPACE, "scan-running")
        assert raw is not None
        assert raw["status"] == "interrupted"
        assert raw["current_drive"] is None
        assert "重新发起扫描" in raw["error"]
        assert comfy_scan.recover_interrupted() == 0

        store.close()
        reopened = job_store.SQLiteJobStore(db_path)
        job_store.set_default_store(reopened)
        comfy_scan._JOB_ID = None

        restored = comfy_scan.state()
        assert restored["id"] == "scan-running"
        assert restored["status"] == "interrupted"
        assert restored["scanned_dirs"] == 321

        stale = dict(restored, status="done", finished_ms=3000)
        comfy_scan._store().save(comfy_scan._NAMESPACE, "scan-running", stale)
        current = reopened.get(comfy_scan._NAMESPACE, "scan-running")
        assert current is not None and current["status"] == "interrupted"
    finally:
        current = job_store.default_store()
        if isinstance(current, job_store.SQLiteJobStore):
            current.close()
        job_store.set_default_store(previous)
        shutil.rmtree(root, ignore_errors=True)

"""卷内移动 / URL 下载作业的持久化与重启边界。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from app.core import job_store
from app.volume_mgr import downloads, moves


@pytest.fixture()
def sqlite_store():
    root = (
        Path(__file__).resolve().parents[2]
        / "data"
        / f"cb-volume-ops-{uuid.uuid4().hex[:10]}"
    )
    root.mkdir(parents=True)
    store = job_store.SQLiteJobStore(str(root / "jobs.sqlite3"))
    previous = job_store.set_default_store(store)
    try:
        yield store
    finally:
        store.close()
        job_store.set_default_store(previous)
        shutil.rmtree(root, ignore_errors=True)


def test_move_job_persists_and_marks_interrupted(sqlite_store):
    job = moves.MoveJob(
        id="move-running",
        volume="comfyui-data",
        dest_dir="models/checkpoints",
        entries=[
            moves.MoveEntry(
                src="input/model.safetensors",
                dst="models/checkpoints/model.safetensors",
                kind="file",
                status="running",
                files_total=1,
            )
        ],
    )
    sqlite_store.create(moves._NAMESPACE, job.id, job.to_dict())

    assert moves.recover_interrupted() == 1
    raw = sqlite_store.get(moves._NAMESPACE, job.id)
    assert raw is not None
    assert raw["status"] == "interrupted"
    assert raw["entries"][0]["status"] == "error"
    assert "不会自动重放" in raw["error"]
    assert "_proc" not in raw
    assert moves.recover_interrupted() == 0

    restored = moves.get_job(job.id)
    assert restored is not None
    assert restored.status == "interrupted"
    assert restored.finished_ms is not None


def test_download_job_persists_and_marks_interrupted(sqlite_store):
    job = downloads.DownloadJob(
        id="download-running",
        url="https://example.com/model.safetensors",
        dest_dir="models/checkpoints",
        filename="model.safetensors",
        volume="comfyui-data",
        log_tail=["downloading"],
    )
    sqlite_store.create(downloads._NAMESPACE, job.id, job.to_dict())

    assert downloads.recover_interrupted() == 1
    raw = sqlite_store.get(downloads._NAMESPACE, job.id)
    assert raw is not None
    assert raw["status"] == "interrupted"
    assert "目标目录可能已有部分文件" in raw["error"]
    assert "_proc" not in raw
    assert downloads.recover_interrupted() == 0

    restored = downloads.get_job(job.id)
    assert restored is not None
    assert restored.display_name == "model.safetensors"
    assert restored.status == "interrupted"

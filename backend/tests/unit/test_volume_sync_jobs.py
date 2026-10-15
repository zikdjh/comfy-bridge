"""模型卷同步作业状态机：部分文件失败必须报 error，不能被 done 掩盖（UI-18）。"""

from __future__ import annotations

import pytest

from app.core import job_store as job_store_mod
from app.modal_volume import jobs as vol_jobs
from app.modal_volume.client import ModalCliError


@pytest.fixture(autouse=True)
def _isolated_store():
    previous = job_store_mod.set_default_store(None)
    yield
    job_store_mod.set_default_store(previous)


@pytest.fixture()
def pull_env(monkeypatch, tmp_path):
    monkeypatch.setattr(vol_jobs, "model_dirs_resolved", lambda: {"checkpoints": str(tmp_path)})

    async def fake_refresh(force: bool = False):
        return {}

    monkeypatch.setattr(vol_jobs.cloud_index, "refresh", fake_refresh)
    return tmp_path


def _job(names: list[str]) -> vol_jobs.SyncJob:
    return vol_jobs.SyncJob(
        id="job-x",
        direction="pull",
        category="checkpoints",
        files=[vol_jobs.SyncFile(name=n) for n in names],
    )


def test_to_dict_counts_done_and_failed() -> None:
    job = _job(["a", "b"])
    job.files[0].status = "done"
    job.files[1].status, job.files[1].error = "error", "boom"
    view = job.to_dict()
    assert view["total"] == 2 and view["done"] == 1 and view["failed"] == 1


@pytest.mark.asyncio
async def test_partial_failure_reports_error(monkeypatch, pull_env) -> None:
    async def fake_get_file(remote, dest, **kwargs):
        if str(remote).endswith("bad.safetensors"):
            raise ModalCliError("目标文件被占用（WinError 5）", category="permission")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    monkeypatch.setattr(vol_jobs.client, "get_file", fake_get_file)
    job = _job(["ok.safetensors", "bad.safetensors"])
    vol_jobs._store().create(vol_jobs._NAMESPACE, job.id, job)

    await vol_jobs._run_job(job)

    assert job.status == "error"  # 有文件失败时不得报 done
    assert job.to_dict()["failed"] == 1
    assert "1/2 个文件失败" in (job.error or "")
    assert job.finished_ms is not None


@pytest.mark.asyncio
async def test_all_success_reports_done(monkeypatch, pull_env) -> None:
    async def fake_get_file(remote, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    monkeypatch.setattr(vol_jobs.client, "get_file", fake_get_file)
    job = _job(["a.safetensors"])
    vol_jobs._store().create(vol_jobs._NAMESPACE, job.id, job)

    await vol_jobs._run_job(job)

    assert job.status == "done"
    assert job.error is None
    assert job.to_dict()["done"] == 1

"""模型卷同步作业引擎：本地 ↔ 云端数据卷（SDK 网关，作业内逐文件顺序执行）。

- push（上传）：本地 model_dirs_resolved()[category] → 卷 models/<category>/
- pull（下载）：卷 models/<category>/<name> → 本地对应目录
作业状态保存在内存（后端重启清空）；取消会取消当前 SDK 协程，已完成文件保留。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import model_dirs_resolved
from ..core.job_store import default_store
from ..models_mgr import cloud_index
from . import client

_NAMESPACE = "modal_volume_sync"
_MAX_FINISHED = 50  # 已完成作业保留上限（每条含全量 files 明细，防止只进不出）


def _store():
    return default_store()


def _trim_jobs() -> None:
    _store().prune(
        _NAMESPACE,
        _MAX_FINISHED,
        is_running=lambda job: job.status == "running",
        created_at=lambda job: job.created_ms,
        include_running=False,
    )


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SyncFile:
    name: str
    status: str = "pending"  # pending | done | error
    error: str | None = None


@dataclass
class SyncJob:
    id: str
    direction: str  # push（本地→云端）| pull（云端→本地）
    category: str
    files: list[SyncFile] = field(default_factory=list)
    status: str = "running"  # running | done | error
    error: str | None = None
    created_ms: int = field(default_factory=now_ms)
    finished_ms: int | None = None
    _cancel: bool = field(default=False, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "category": self.category,
            "status": self.status,
            "error": self.error,
            "created_ms": self.created_ms,
            "finished_ms": self.finished_ms,
            "total": len(self.files),
            "done": sum(1 for f in self.files if f.status == "done"),
            "failed": sum(1 for f in self.files if f.status == "error"),
            "files": [
                {"name": f.name, "status": f.status, "error": f.error}
                for f in self.files
            ],
        }


def get_job(job_id: str) -> SyncJob | None:
    return _store().get(_NAMESPACE, job_id)


def list_jobs(limit: int = 50) -> list[SyncJob]:
    jobs = sorted(_store().list(_NAMESPACE), key=lambda j: j.created_ms, reverse=True)
    return jobs[:limit]


def create_job(direction: str, category: str, names: list[str]) -> SyncJob:
    """创建并启动同步作业；路径合法性在此校验（拒绝目录穿越 / 非法分类）。"""
    resolved = model_dirs_resolved()
    if category not in resolved:
        raise ValueError(f"未知模型分类: {category}")
    if direction not in {"push", "pull"}:
        raise ValueError(f"非法方向: {direction}")
    clean: list[str] = []
    for name in names:
        rel = Path(name.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            continue
        clean.append(rel.as_posix())
    if not clean:
        raise ValueError("没有有效的文件名")

    job = SyncJob(
        id=uuid.uuid4().hex[:12],
        direction=direction,
        category=category,
        files=[SyncFile(name=n) for n in clean],
    )
    _store().create(_NAMESPACE, job.id, job)
    _trim_jobs()
    job._task = asyncio.get_running_loop().create_task(_run_job(job))
    return job


async def cancel_job(job_id: str) -> bool:
    job = _store().get(_NAMESPACE, job_id)
    if job is None or job.status != "running":
        return False
    job._cancel = True
    if job._task is not None and not job._task.done():
        job._task.cancel()
    _store().save(_NAMESPACE, job_id, job)
    return True


async def _run_job(job: SyncJob) -> None:
    local_root = Path(model_dirs_resolved()[job.category])
    try:
        for f in job.files:
            if job._cancel:
                job.status = "error"
                job.error = "已被取消"
                break
            try:
                if job.direction == "push":
                    src = local_root / f.name
                    if not src.is_file():
                        f.status, f.error = "error", "本地文件不存在"
                        _store().save(_NAMESPACE, job.id, job)
                        continue
                    remote = f"models/{job.category}/{f.name}"
                    await client.put_file(src, remote, force=True)
                else:
                    dest = local_root / f.name
                    remote = f"models/{job.category}/{f.name}"
                    await client.get_file(remote, dest, force=True)
                f.status = "done"
            except asyncio.CancelledError:
                f.status, f.error = "error", "已被取消"
                job.status = "error"
                job.error = "已被取消"
                _store().save(_NAMESPACE, job.id, job)
                break
            except Exception as exc:  # noqa: BLE001 —— 单文件失败不拖垮作业
                f.status, f.error = "error", str(exc)[:300]
            _store().save(_NAMESPACE, job.id, job)
    finally:
        if job.status == "running":
            failed = [f for f in job.files if f.status == "error"]
            if failed:
                # 有文件失败时不能报 done：「已完成」会让用户以为文件已落盘
                job.status = "error"
                job.error = f"{len(failed)}/{len(job.files)} 个文件失败：{failed[0].error or ''}"
            else:
                job.status = "done"
        if job.finished_ms is None:
            job.finished_ms = now_ms()
        _store().save(_NAMESPACE, job.id, job)
        try:  # 同步完成后云端清单已变，强制重建模型索引
            await cloud_index.refresh(force=True)
        except Exception as exc:  # noqa: BLE001 —— 重建失败不影响作业结果
            job.error = job.error or f"云端模型索引重建失败: {exc}"
            _store().save(_NAMESPACE, job.id, job)

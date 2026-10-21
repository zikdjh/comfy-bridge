"""模型卷同步作业引擎：本地 ↔ 云端数据卷（SDK 网关，作业内逐文件顺序执行）。

- push（上传）：本地 model_dirs_resolved()[category] → 卷 models/<category>/
- pull（下载）：卷 models/<category>/<name> → 本地对应目录
作业快照写入 JobStore；取消会取消当前 SDK 协程，已完成文件保留。后端重启后
运行态收敛为 interrupted，不自动重放无法确认落卷结果的写操作。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import model_dirs_resolved
from ..core.job_store import SQLiteJobStore, default_store
from ..models_mgr import cloud_index
from . import client

_NAMESPACE = "modal_volume_sync"
_MAX_FINISHED = 50  # 已完成作业保留上限（每条含全量 files 明细，防止只进不出）
_TASKS: dict[str, asyncio.Task] = {}


def _store():
    store = default_store()
    if isinstance(store, SQLiteJobStore):
        store.set_terminal_predicate(
            lambda job: (
                job.status if isinstance(job, SyncJob) else job.get("status")
            ) in {"done", "error", "interrupted"},
            namespace=_NAMESPACE,
        )
    return store


def _save(job: SyncJob) -> None:
    _store().save(_NAMESPACE, job.id, job.to_dict())


def _trim_jobs() -> None:
    _store().prune(
        _NAMESPACE,
        _MAX_FINISHED,
        is_running=lambda job: (
            job.status if isinstance(job, SyncJob) else job.get("status")
        ) == "running",
        created_at=lambda job: (
            job.created_ms if isinstance(job, SyncJob) else job.get("created_ms", 0)
        ),
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
    status: str = "running"  # running | done | error | interrupted
    error: str | None = None
    created_ms: int = field(default_factory=now_ms)
    finished_ms: int | None = None
    cancel_requested: bool = False
    _task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "category": self.category,
            "status": self.status,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
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

    @classmethod
    def from_dict(cls, data: dict) -> SyncJob:
        files = [
            SyncFile(
                name=str(item.get("name") or ""),
                status=str(item.get("status") or "pending"),
                error=item.get("error"),
            )
            for item in data.get("files", [])
            if isinstance(item, dict)
        ]
        finished = data.get("finished_ms")
        return cls(
            id=str(data.get("id") or ""),
            direction=str(data.get("direction") or ""),
            category=str(data.get("category") or ""),
            files=files,
            status=str(data.get("status") or "running"),
            error=data.get("error"),
            created_ms=int(data.get("created_ms") or now_ms()),
            finished_ms=int(finished) if finished is not None else None,
            cancel_requested=bool(data.get("cancel_requested") or False),
        )


def _coerce_job(value: SyncJob | dict) -> SyncJob:
    return value if isinstance(value, SyncJob) else SyncJob.from_dict(value)


def get_job(job_id: str) -> SyncJob | None:
    value = _store().get(_NAMESPACE, job_id)
    return _coerce_job(value) if value is not None else None


def list_jobs(limit: int = 50) -> list[SyncJob]:
    jobs = sorted(
        (_coerce_job(value) for value in _store().list(_NAMESPACE)),
        key=lambda job: job.created_ms,
        reverse=True,
    )
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
    _store().create(_NAMESPACE, job.id, job.to_dict())
    _trim_jobs()
    job._task = asyncio.get_running_loop().create_task(_run_job(job))
    _TASKS[job.id] = job._task
    return job


async def cancel_job(job_id: str) -> bool:
    job = get_job(job_id)
    if job is None or job.status != "running":
        return False
    job.cancel_requested = True
    _save(job)
    task = _TASKS.get(job_id)
    if task is not None and not task.done():
        task.cancel()
    return True


def recover_interrupted() -> int:
    """启动恢复：只收敛状态，不自动重放无法确认落卷结果的写操作。"""
    changed = 0
    for value in _store().list(_NAMESPACE):
        job = _coerce_job(value)
        if job.status != "running":
            continue
        job.status = "interrupted"
        job.error = (
            "桥接器重启，模型卷同步已中断；已完成文件保留，"
            "未完成项需核对云端与本地文件后显式重试"
        )
        job.finished_ms = now_ms()
        _save(job)
        changed += 1
    return changed


async def _run_job(job: SyncJob) -> None:
    local_root = Path(model_dirs_resolved()[job.category])
    try:
        for f in job.files:
            if job.cancel_requested:
                job.status = "error"
                job.error = "已被取消"
                break
            try:
                if job.direction == "push":
                    src = local_root / f.name
                    if not src.is_file():
                        f.status, f.error = "error", "本地文件不存在"
                        _save(job)
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
                break
            except Exception as exc:  # noqa: BLE001 —— 单文件失败不拖垮作业
                f.status, f.error = "error", str(exc)[:300]
            _save(job)
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
        _TASKS.pop(job.id, None)
        try:  # 同步完成后云端清单已变，强制重建模型索引
            await cloud_index.refresh(force=True)
        except Exception as exc:  # noqa: BLE001 —— 重建失败不影响作业结果
            job.error = job.error or f"云端模型索引重建失败: {exc}"
        _save(job)
        _trim_jobs()

"""模型卷同步作业引擎：本地 ↔ 云端数据卷（modal CLI 子进程，作业内逐文件顺序执行）。

- push（上传）：本地 model_dirs_resolved()[category] → 卷 models/<category>/
- pull（下载）：卷 models/<category>/<name> → 本地对应目录
作业状态保存在内存（后端重启清空）；取消在文件间生效，正在传的单个文件会被终止。
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import model_dirs_resolved
from ..models_mgr import cloud_index
from . import client

_JOBS: dict[str, "SyncJob"] = {}
_MAX_FINISHED = 50  # 已完成作业保留上限（每条含全量 files 明细，防止只进不出）


def _trim_jobs() -> None:
    finished = sorted(
        (j for j in _JOBS.values() if j.status != "running"),
        key=lambda j: j.created_ms,
    )
    for j in finished[: max(0, len(finished) - _MAX_FINISHED)]:
        _JOBS.pop(j.id, None)


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
    _proc: subprocess.Popen | None = field(default=None, repr=False)

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
    return _JOBS.get(job_id)


def list_jobs(limit: int = 50) -> list[SyncJob]:
    jobs = sorted(_JOBS.values(), key=lambda j: j.created_ms, reverse=True)
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
    _JOBS[job.id] = job
    _trim_jobs()
    asyncio.get_running_loop().create_task(_run_job(job))
    return job


async def cancel_job(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if job is None or job.status != "running":
        return False
    job._cancel = True
    proc = job._proc
    if proc is not None and proc.poll() is None:
        proc.terminate()
    return True


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()


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
                        continue
                    remote = f"models/{job.category}/{f.name}"
                    proc = client._popen(
                        "volume", "put", client.volume_name(), "-f", str(src), remote
                    )
                else:
                    dest_dir = local_root / Path(f.name).parent
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    remote = f"models/{job.category}/{f.name}"
                    proc = client._popen(
                        "volume", "get", client.volume_name(), remote, str(dest_dir)
                    )
                job._proc = proc
                out, err = await asyncio.to_thread(proc.communicate)
                job._proc = None
                if proc.returncode != 0:
                    f.status = "error"
                    f.error = (err or out).decode("utf-8", errors="replace").strip()[:300]
                else:
                    f.status = "done"
            except Exception as exc:  # noqa: BLE001 —— 单文件失败不拖垮作业
                f.status, f.error = "error", str(exc)[:300]
    finally:
        if job._proc is not None:
            _terminate(job._proc)
            job._proc = None
        if job.status == "running":
            job.status = "done"
            failed = [f for f in job.files if f.status == "error"]
            if failed:
                job.error = f"{len(failed)}/{len(job.files)} 个文件失败：{failed[0].error or ''}"
        if job.finished_ms is None:
            job.finished_ms = now_ms()
        try:  # 同步完成后云端清单已变，强制重建模型索引
            await cloud_index.refresh(force=True)
        except Exception as exc:  # noqa: BLE001 —— 重建失败不影响作业结果
            job.error = job.error or f"云端模型索引重建失败: {exc}"

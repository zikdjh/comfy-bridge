"""数据卷内移动作业引擎：modal run 临时容器内完成服务端复制 + 删源。

- 每个作业 = 一个 `modal run modal-runner/volume_ops.py::move_entries` 子进程，
  云端 debian_slim 容器内经 FUSE 挂载复制条目到目标目录后 vol.remove_file 删源
  （V1 卷无服务端 rename/copy 原语，此为唯一可靠搬移路径，不走本地带宽）；
- stdout 逐行 JSON 泵进度（entry_start / file_progress / entry_done）；
- 作业快照写入 JobStore；后端重启后运行态收敛为 interrupted，不自动重放；
- 取消 = 终止本地 CLI（云端函数随 ephemeral
  app 一并停止，当前条目可能半复制——源保留、目标残留，错误信息中提示）；
- 移动合法性（存在性/自包含/同名冲突/结构目录保护）在 API 创建时校验。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field

from .. import runner_assets
from ..core.job_store import SQLiteJobStore, default_store
from ..modal_volume import client as modal_cli
from . import usage

# volume_ops.py：仓库布局优先，wheel 安装回落包内副本（app/runner_assets.py）
_OPS_FILE = runner_assets.runner_file("volume_ops.py")
_LOG_KEEP = 20          # 每作业保留的最近输出行数
_JOBS_KEEP = 50         # 作业表保留上限
_NAMESPACE = "modal_volume_move"
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
_PROCS: dict[str, subprocess.Popen] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class MoveEntry:
    src: str
    dst: str
    kind: str  # dir | file
    status: str = "pending"  # pending | running | done | error
    error: str | None = None
    files_total: int = 0
    files_done: int = 0


@dataclass
class MoveJob:
    id: str
    volume: str
    dest_dir: str  # "" = 卷根
    entries: list[MoveEntry] = field(default_factory=list)
    status: str = "running"  # running | done | error | interrupted
    error: str | None = None
    log_tail: list[str] = field(default_factory=list)
    created_ms: int = field(default_factory=now_ms)
    finished_ms: int | None = None
    cancel_requested: bool = False
    _proc: subprocess.Popen | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "volume": self.volume,
            "dest_dir": self.dest_dir,
            "status": self.status,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "log_tail": self.log_tail,
            "created_ms": self.created_ms,
            "finished_ms": self.finished_ms,
            "entries": [
                {
                    "src": e.src,
                    "dst": e.dst,
                    "kind": e.kind,
                    "status": e.status,
                    "error": e.error,
                    "files_total": e.files_total,
                    "files_done": e.files_done,
                }
                for e in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> MoveJob:
        finished = data.get("finished_ms")
        return cls(
            id=str(data.get("id") or ""),
            volume=str(data.get("volume") or ""),
            dest_dir=str(data.get("dest_dir") or ""),
            entries=[
                MoveEntry(
                    src=str(item.get("src") or ""),
                    dst=str(item.get("dst") or ""),
                    kind=str(item.get("kind") or ""),
                    status=str(item.get("status") or "pending"),
                    error=item.get("error"),
                    files_total=int(item.get("files_total") or 0),
                    files_done=int(item.get("files_done") or 0),
                )
                for item in data.get("entries", [])
                if isinstance(item, dict)
            ],
            status=str(data.get("status") or "running"),
            error=data.get("error"),
            log_tail=[str(line) for line in data.get("log_tail", [])][-_LOG_KEEP:],
            created_ms=int(data.get("created_ms") or now_ms()),
            finished_ms=int(finished) if finished is not None else None,
            cancel_requested=bool(data.get("cancel_requested") or False),
        )


def _store():
    store = default_store()
    if isinstance(store, SQLiteJobStore):
        store.set_terminal_predicate(
            lambda job: (
                job.status if isinstance(job, MoveJob) else job.get("status")
            ) in {"done", "error", "interrupted"},
            namespace=_NAMESPACE,
        )
    return store


def _save(job: MoveJob) -> None:
    _store().save(_NAMESPACE, job.id, job.to_dict())


def _coerce_job(value: MoveJob | dict) -> MoveJob:
    return value if isinstance(value, MoveJob) else MoveJob.from_dict(value)


def list_jobs(limit: int = 30) -> list[MoveJob]:
    jobs = sorted(
        (_coerce_job(value) for value in _store().list(_NAMESPACE)),
        key=lambda job: job.created_ms,
        reverse=True,
    )
    return jobs[:limit]


def get_job(job_id: str) -> MoveJob | None:
    value = _store().get(_NAMESPACE, job_id)
    return _coerce_job(value) if value is not None else None


def create_move(volume: str, dest_dir: str, entries: list[dict]) -> MoveJob:
    """创建并启动移动作业；entries = [{src, dst, kind}]（合法性已在 API 层校验）。"""
    if not _NAME_RE.match(volume):
        raise ValueError(f"非法的数据卷名: {volume!r}")
    if not entries:
        raise ValueError("没有可移动的条目")
    if not _OPS_FILE.is_file():
        raise RuntimeError(f"找不到 volume_ops 脚本: {_OPS_FILE}")
    job = MoveJob(
        id=uuid.uuid4().hex[:12],
        volume=volume,
        dest_dir=dest_dir,
        entries=[MoveEntry(src=e["src"], dst=e["dst"], kind=e["kind"]) for e in entries],
    )
    _store().create(_NAMESPACE, job.id, job.to_dict())
    _trim_jobs()
    asyncio.get_running_loop().create_task(_run(job))
    return job


async def cancel_move(job_id: str) -> bool:
    job = get_job(job_id)
    if job is None or job.status != "running":
        return False
    job.cancel_requested = True
    _save(job)
    proc = _PROCS.get(job_id)
    if proc is not None and proc.poll() is None:
        proc.terminate()
    return True


def recover_interrupted() -> int:
    """重启只收敛状态；移动是复制后删源，不能自动重放。"""
    changed = 0
    for value in _store().list(_NAMESPACE):
        job = _coerce_job(value)
        if job.status != "running":
            continue
        job.status = "interrupted"
        job.error = (
            "桥接器重启，卷内移动已中断；源路径通常保留，目标目录可能已有残留。"
            "请核对两端文件后显式重试，程序不会自动重放"
        )
        for entry in job.entries:
            if entry.status in {"pending", "running"}:
                entry.status = "error"
                entry.error = "桥接器重启，执行结果不确定"
        job.finished_ms = now_ms()
        _save(job)
        changed += 1
    return changed


def _trim_jobs() -> None:
    _store().prune(
        _NAMESPACE,
        _JOBS_KEEP,
        is_running=lambda job: (
            job.status if isinstance(job, MoveJob) else job.get("status")
        ) == "running",
        created_at=lambda job: (
            job.created_ms if isinstance(job, MoveJob) else job.get("created_ms", 0)
        ),
        include_running=False,
    )


def _log(job: MoveJob, text: str) -> None:
    for chunk in text.replace("\r", "\n").split("\n"):
        chunk = chunk.strip()
        if chunk:
            job.log_tail.append(chunk[:200])
    if len(job.log_tail) > _LOG_KEEP:
        job.log_tail[:] = job.log_tail[-_LOG_KEEP:]


def _absorb(job: MoveJob, line: str) -> None:
    """解析云端脚本的进度 JSON 行（entry_start/file_progress/entry_done）。"""
    line = line.strip()
    _log(job, line)
    if not line.startswith("{"):
        return
    try:
        msg = json.loads(line)
    except ValueError:
        return
    etype = msg.get("type")
    src = str(msg.get("src") or "")
    entry = next((e for e in job.entries if e.src == src), None)
    if entry is None:
        return
    if etype == "entry_start":
        entry.status = "running"
        entry.files_total = int(msg.get("files_total") or 0)
    elif etype == "file_progress":
        entry.status = "running"
        entry.files_done = int(msg.get("files_done") or 0)
    elif etype == "entry_done":
        entry.status = "done" if msg.get("ok") else "error"
        entry.error = msg.get("error") or None
        if entry.status == "done" and not entry.files_done:
            entry.files_done = entry.files_total  # 快条目没来得及报 file_progress
    if etype in {"entry_start", "file_progress", "entry_done"}:
        _save(job)


async def _run(job: MoveJob) -> None:
    try:
        if job.cancel_requested:
            job.status = "error"
            job.error = "已被取消（尚未开始移动）"
            return
        payload = json.dumps([[e.src, e.dst, e.kind] for e in job.entries])
        # 挂载卷重定向：modal run 本地导入 volume_ops.py 时读取该环境变量
        proc = modal_cli._popen(
            "run", f"{_OPS_FILE}::move_entries",
            "--entries-json", payload,
            extra_env={"CB_MODAL_VOLUME": job.volume},
        )
        job._proc = proc
        _PROCS[job.id] = proc
        if job.cancel_requested:
            proc.terminate()

        async def pump(pipe) -> None:
            while True:
                line = await asyncio.to_thread(pipe.readline)
                if not line:
                    break
                _absorb(job, line.decode("utf-8", errors="replace"))

        out_task = asyncio.create_task(pump(proc.stdout))
        err_task = asyncio.create_task(pump(proc.stderr))
        while proc.poll() is None:
            await asyncio.sleep(1)  # 轮询等待；wait(timeout) 超时会抛 TimeoutExpired
        for t in (out_task, err_task):
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=10)
            except (TimeoutError, Exception):  # noqa: BLE001
                t.cancel()
        job._proc = None

        unfinished = [e for e in job.entries if e.status in ("pending", "running")]
        if job.cancel_requested:
            job.status = "error"
            job.error = "已被取消（当前条目可能半复制：源保留，目标目录有残留）"
            for e in unfinished:
                e.status = "error"
                e.error = "已被取消"
        elif proc.returncode != 0:
            job.status = "error"
            tail = job.log_tail[-1] if job.log_tail else ""
            job.error = (tail or f"modal run 退出码 {proc.returncode}")[:300]
            for e in unfinished:
                e.status = "error"
                e.error = "云端容器提前退出"
        else:
            for e in unfinished:  # 退出码 0 但没收到终态（理论不应发生）
                e.status = "error"
                e.error = "云端容器提前退出"
            failed = [e for e in job.entries if e.status == "error"]
            job.status = "error" if failed else "done"
            if failed:
                job.error = f"{len(failed)}/{len(job.entries)} 个条目失败：{failed[0].error or ''}"
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = str(exc)[:300]
    finally:
        if job._proc is not None and job._proc.poll() is None:
            job._proc.terminate()
        job._proc = None
        _PROCS.pop(job.id, None)
        if job.finished_ms is None:
            job.finished_ms = now_ms()
        _save(job)
        _trim_jobs()
        with contextlib.suppress(Exception):  # 统计失败不影响作业结果
            usage.get_usage(force=True)

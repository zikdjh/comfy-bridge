"""数据卷 URL 下载作业引擎：modal run download_model 后台子进程（内存态作业表）。

- 每个作业 = 一个 `modal run modal-runner/runner.py::download_model` 子进程，
  云端在 CPU 容器里把直链 URL / HF 仓库下载进数据卷指定目录并 commit；
- 作业状态保存在内存（后端重启清空）；取消 = 终止本地 CLI（云端函数随 ephemeral
  app 一并停止，尽力而为）；
- 下载目标目录与文件名的合法性在创建时校验（拒绝穿越 / 非 http(s) 链接）。
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field

from .. import runner_assets
from ..modal_volume import client as modal_cli

# runner.py：仓库布局优先，wheel 安装回落包内副本（app/runner_assets.py）
_RUNNER_FILE = runner_assets.runner_file("runner.py")
_LOG_KEEP = 12          # 每作业保留的最近输出行数
_JOBS_KEEP = 50         # 作业表保留上限（超出淘汰最旧已完成作业）
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")   # 与 API 侧卷名校验一致
_JOBS: dict[str, "DownloadJob"] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_rel_dir(path: str) -> str:
    """校验并归一化数据卷相对目录；非法抛 ValueError。"""
    rel = (path or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/") or rel.split("/")[0].startswith("."):
        raise ValueError(f"必须是数据卷内的相对目录，收到: {path!r}")
    return rel


@dataclass
class DownloadJob:
    id: str
    url: str
    dest_dir: str
    filename: str | None
    volume: str = ""
    status: str = "running"  # running | done | error
    error: str | None = None
    log_tail: list[str] = field(default_factory=list)  # 最近的 CLI 输出（截断）
    result_files: list[str] = field(default_factory=list)  # 完成后目标目录内的文件
    created_ms: int = field(default_factory=now_ms)
    finished_ms: int | None = None
    _cancel: bool = field(default=False, repr=False)
    _proc: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def display_name(self) -> str:
        if self.filename:
            return self.filename
        base = self.url.split("://", 1)[-1].rstrip("/").rsplit("/", 1)[-1]
        return base or self.url

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "name": self.display_name,
            "dest_dir": self.dest_dir,
            "filename": self.filename,
            "volume": self.volume,
            "status": self.status,
            "error": self.error,
            "log_tail": self.log_tail,
            "result_files": self.result_files,
            "created_ms": self.created_ms,
            "finished_ms": self.finished_ms,
        }


def list_jobs(limit: int = 30) -> list[DownloadJob]:
    jobs = sorted(_JOBS.values(), key=lambda j: j.created_ms, reverse=True)
    return jobs[:limit]


def get_job(job_id: str) -> DownloadJob | None:
    return _JOBS.get(job_id)


def create_download(url: str, dest_dir: str, filename: str | None = None,
                    volume: str | None = None) -> DownloadJob:
    """创建并启动一个云端下载作业；参数非法抛 ValueError。

    volume 为空时落到主卷（comfyui-data）；任意卷经 CB_MODAL_VOLUME 环境变量在
    modal run 本地导入 runner.py 时重定向挂载（部署的主应用不受影响）。
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("下载链接必须是 http(s) URL")
    dest = safe_rel_dir(dest_dir)
    vol = (volume or "").strip() or modal_cli.volume_name()
    if not _NAME_RE.match(vol):
        raise ValueError(f"非法的数据卷名: {vol!r}")
    name = (filename or "").strip() or None
    if name and ("/" in name or "\\" in name or name in {".", ".."}):
        raise ValueError(f"保存文件名不能包含路径分隔符: {name!r}")
    if not _RUNNER_FILE.is_file():
        raise RuntimeError(f"找不到 runner 脚本: {_RUNNER_FILE}")

    job = DownloadJob(
        id=uuid.uuid4().hex[:12],
        url=url,
        dest_dir=dest,
        filename=name,
        volume=vol,
    )
    _JOBS[job.id] = job
    _trim_jobs()
    asyncio.get_running_loop().create_task(_run(job))
    return job


async def cancel_download(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if job is None or job.status != "running":
        return False
    job._cancel = True
    proc = job._proc
    if proc is not None and proc.poll() is None:
        proc.terminate()
    return True


def _trim_jobs() -> None:
    finished = [j for j in _JOBS.values() if j.status != "running"]
    if len(_JOBS) <= _JOBS_KEEP:
        return
    for job in sorted(finished, key=lambda j: j.created_ms)[: len(_JOBS) - _JOBS_KEEP]:
        _JOBS.pop(job.id, None)


def _absorb_output(job: DownloadJob, text: str) -> None:
    """把 CLI 输出并入 log_tail（tqdm 的 \\r 帧也按片段截取，保留最新进度）。"""
    for chunk in text.replace("\r", "\n").split("\n"):
        chunk = chunk.strip()
        if chunk:
            job.log_tail.append(chunk[:200])
    if len(job.log_tail) > _LOG_KEEP:
        job.log_tail[:] = job.log_tail[-_LOG_KEEP:]


async def _run(job: DownloadJob) -> None:
    try:
        cmd = [
            "run", f"{_RUNNER_FILE}::download_model",
            "--source", job.url,
            "--dest", job.dest_dir,
        ]
        if job.filename:
            cmd += ["--filename", job.filename]
        # 挂载卷重定向：modal run 本地导入 runner.py 时读取该环境变量
        proc = modal_cli._popen(*cmd, extra_env={"CB_MODAL_VOLUME": job.volume})
        job._proc = proc

        async def pump(pipe, sink: list) -> None:
            while True:
                line = await asyncio.to_thread(pipe.readline)
                if not line:
                    break
                _absorb_output(job, line.decode("utf-8", errors="replace"))

        out_task = asyncio.create_task(pump(proc.stdout, job.log_tail))
        err_task = asyncio.create_task(pump(proc.stderr, job.log_tail))
        while proc.poll() is None:
            await asyncio.sleep(1)  # 轮询等待；不要用 wait(timeout) —— 超时会抛 TimeoutExpired
        # 等待输出管道排空（最多 10s）
        for t in (out_task, err_task):
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                t.cancel()
        job._proc = None

        if job._cancel:
            job.status = "error"
            job.error = "已被取消"
        elif proc.returncode != 0:
            job.status = "error"
            job.error = (job.log_tail[-1] if job.log_tail else f"modal run 退出码 {proc.returncode}")
        else:
            job.status = "done"
            try:  # 完成后列出目标目录，展示落卷文件
                entries = await modal_cli.list_dir(job.dest_dir, volume=job.volume)
                job.result_files = [e["name"] for e in entries if e["type"] in ("file", "dir")][:50]
            except Exception:  # noqa: BLE001 —— 清单失败不影响下载结果
                pass
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = str(exc)[:300]
    finally:
        if job._proc is not None and job._proc.poll() is None:
            job._proc.terminate()
        job._proc = None
        if job.finished_ms is None:
            job.finished_ms = now_ms()
        _trim_jobs()

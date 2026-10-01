"""共享的 modal deploy 后台任务（M2-2 镜像固化 / M2-5 版本对齐共用，单飞互斥）。

两类功能都要「改文件 → modal deploy 重建镜像」，共用同一任务状态与互斥锁：
同时只允许一个 deploy 在跑（并发发起由 begin_exclusive 抛 RuntimeError，API 转 409）。
失败回滚由调用方注入 rollback 回调（非零退出 / CLI 缺失时调用）；超时属不确定态
（镜像可能仍在远端构建），不回滚，保留新文件并在错误信息里说明。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time

from .. import runner_assets
from ..modal_volume.client import auth_env

RUNNER_FILE = runner_assets.runner_file("runner.py")

_DEPLOY_TIMEOUT = 1800.0        # 镜像重建兜底 30 分钟（实测热缓存 ~80s）
_LOG_TAIL_LINES = 14

# 部署任务状态（单任务互斥；后端重启即清空，与 comfy_scan 同口径）
_JOB: dict = {
    "state": "idle",            # idle | running | ok | error
    "started_ms": None,
    "finished_ms": None,
    "label": None,              # 本次部署目的（如 "版本对齐 → v0.38.0"）
    "error": None,
    "log_tail": [],
}
_JOB_LOCK = asyncio.Lock()
_TASK: asyncio.Task | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def job_state() -> dict:
    return dict(_JOB, log_tail=list(_JOB["log_tail"]))


def running() -> bool:
    return _JOB["state"] == "running"


async def begin_exclusive(label: str) -> dict:
    """置 running 并返回快照；已有部署在跑抛 RuntimeError（API 层转 409）。"""
    async with _JOB_LOCK:
        if _JOB["state"] == "running":
            raise RuntimeError("已有镜像部署任务在进行中")
        _JOB.update(
            state="running", started_ms=_now_ms(), finished_ms=None,
            label=label, error=None,
            log_tail=[f"部署目标：{label}"],
        )
        return job_state()


def finish_noop(message: str) -> dict:
    """无需真正 deploy 时把任务置为 ok（如版本 pin 已一致）。"""
    _JOB.update(state="ok", finished_ms=_now_ms(), error=None, log_tail=[message])
    return job_state()


def _output_tail(out: bytes, err: bytes) -> list[str]:
    lines = [
        *err.decode("utf-8", errors="replace").splitlines(),
        *out.decode("utf-8", errors="replace").splitlines(),
    ]
    keep = [
        ln[:220] for ln in lines
        if ln.strip() and "DeprecationWarning" not in ln and "set_event_loop_policy" not in ln
    ]
    return keep[-_LOG_TAIL_LINES:]


async def run_deploy(rollback) -> None:
    """执行 modal deploy（后台任务体，调用方 create_task）；失败调 rollback 并置 error。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            shutil.which("modal") or "modal", "deploy", str(RUNNER_FILE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONWARNINGS": "ignore", **auth_env()},
        )
    except (FileNotFoundError, OSError) as exc:
        if rollback:
            rollback()
        _JOB.update(state="error", finished_ms=_now_ms(),
                    error=f"无法启动 modal CLI：{exc}", log_tail=[])
        return
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_DEPLOY_TIMEOUT)
    except TimeoutError:
        proc.kill()
        _JOB.update(
            state="error", finished_ms=_now_ms(),
            error=(f"modal deploy 超时（>{_DEPLOY_TIMEOUT:g}s）已终止；远端镜像可能仍在构建，"
                   "新文件已保留，可稍后重新发起或手动 `modal app list` 核对"),
            log_tail=["[超时终止]"],
        )
        return
    tail = _output_tail(out, err)
    if proc.returncode != 0:
        if rollback:
            rollback()
        _JOB.update(
            state="error", finished_ms=_now_ms(),
            error=f"modal deploy 失败（退出码 {proc.returncode}），已回滚本次变更",
            log_tail=tail,
        )
        return
    _JOB.update(state="ok", finished_ms=_now_ms(), error=None, log_tail=tail)


def spawn(rollback) -> None:
    """create_task 包装（保持任务引用防 GC）。"""
    global _TASK
    _TASK = asyncio.create_task(run_deploy(rollback))

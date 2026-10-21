"""共享的 modal deploy 后台任务（M2-2 镜像固化 / M2-5 版本对齐共用，单飞互斥）。

两类功能都要「改文件 → modal deploy 重建镜像」，共用同一任务状态与互斥锁：
同时只允许一个 deploy 在跑（并发发起由 begin_exclusive 抛 RuntimeError，API 转 409）。
失败回滚由调用方注入 rollback 回调（非零退出 / CLI 缺失时调用）；超时属不确定态
（镜像可能仍在远端构建），不回滚，保留新文件并在错误信息里说明。任务快照写入
JobStore；进程重启后运行态收敛为 interrupted，不自动重放远端变更。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable

from .. import runner_assets
from ..core.config import get_settings
from ..core.job_store import SQLiteJobStore, default_store
from ..events import publish
from ..modal_volume.client import cli_argv, cli_env

RUNNER_FILE = runner_assets.runner_file("runner.py")


def resource_env() -> dict[str, str]:
    """部署期资源规格：runner.py 在 modal deploy 时读取并写入 @app.function。

    Modal 的 gpu/cpu/memory 只能在 deploy 时确定，因此「设置 → Modal → 云端资源
    规格」保存后必须触发一次部署才生效；本函数是配置到部署的唯一注入点。
    """
    modal = get_settings().modal
    return {
        "CB_MODAL_GPU": (modal.gpu or "").strip() or "L40S",
        "CB_MODAL_CPU": f"{modal.cpu:g}",
        "CB_MODAL_MEMORY_MB": str(int(modal.memory_mb)),
    }

_DEPLOY_TIMEOUT = 1800.0        # 镜像重建兜底 30 分钟（实测热缓存 ~80s）
_LOG_TAIL_LINES = 14
_NAMESPACE = "nodes_deploy"
_TERMINAL_STATES = {"ok", "error", "interrupted"}
_INTERRUPTED_ERROR = (
    "桥接器重启，部署结果不确定；本次新文件已保留，远端镜像可能仍在构建或已生效。"
    "请先核对应用状态与部署时间，并查看部署与镜像日志；确认结果前不要重复部署，"
    "也不要回滚无法确认的远端变更"
)

# 部署任务快照持久化到 JobStore；Task、子进程和一次性回调只留在进程内。
_JOB_ID: str | None = None
_IDLE_JOB: dict = {
    "id": None,
    "state": "idle",            # idle | running | ok | error | interrupted
    "created_ms": None,
    "started_ms": None,
    "finished_ms": None,
    "label": None,              # 本次部署目的（如 "版本对齐 → v0.38.0"）
    "error": None,
    "log_tail": [],
}
_JOB_LOCK = asyncio.Lock()
_TASK: asyncio.Task | None = None

# 一次性收尾回调：调用方（版本对齐/镜像重建）在 begin_exclusive 后注册，run_deploy
# 收尾时以 (outcome, error) 回调一次——outcome ∈ ok / rolled_back / timeout。用于
# 版本对齐历史留痕（version_align.sink_for），fire 后即清除。
_SINK: Callable[[str, str | None], None] | None = None


def _store():
    store = default_store()
    if isinstance(store, SQLiteJobStore):
        store.set_terminal_predicate(
            lambda job: job.get("state") in _TERMINAL_STATES,
            namespace=_NAMESPACE,
        )
    return store


def _copy_job(job: dict) -> dict:
    return {**job, "log_tail": list(job.get("log_tail") or [])}


def _latest_job() -> dict | None:
    jobs = _store().list(_NAMESPACE)
    if not jobs:
        return None
    return max(jobs, key=lambda job: float(job.get("created_ms") or 0))


def _current_job(*, create_if_missing: bool = False) -> dict | None:
    global _JOB_ID
    if _JOB_ID is not None:
        stored = _store().get(_NAMESPACE, _JOB_ID)
        if stored is not None:
            return _copy_job(stored)
    latest = _latest_job()
    if latest is not None:
        _JOB_ID = str(latest.get("id") or "") or None
        return _copy_job(latest)
    if not create_if_missing:
        return None
    _JOB_ID = uuid.uuid4().hex[:12]
    job = {
        **_IDLE_JOB,
        "id": _JOB_ID,
        "state": "running",
        "created_ms": _now_ms(),
        "started_ms": _now_ms(),
        "label": "modal deploy",
        "log_tail": ["部署目标：modal deploy"],
    }
    return _copy_job(_store().create(_NAMESPACE, _JOB_ID, job))


def _save_job(job: dict) -> dict:
    global _JOB_ID
    job_id = str(job.get("id") or "")
    if not job_id:
        raise ValueError("部署快照缺少作业 ID")
    stored = _store().save(_NAMESPACE, job_id, job)
    canonical = _copy_job(stored if isinstance(stored, dict) else job)
    _JOB_ID = job_id
    return canonical


def set_finish_sink(fn: Callable[[str, str | None], None] | None) -> None:
    global _SINK
    _SINK = fn


def _fire_sink(outcome: str, error: str | None) -> None:
    global _SINK
    fn, _SINK = _SINK, None
    if fn is not None:
        with contextlib.suppress(Exception):  # 留痕失败不影响部署任务状态
            fn(outcome, error)


def _now_ms() -> int:
    return int(time.time() * 1000)


def job_state() -> dict:
    return _current_job() or _copy_job(_IDLE_JOB)


def _publish_job(action: str, job: dict | None = None) -> None:
    publish("deploy", {"action": action, "job": job or job_state()})


def running() -> bool:
    return any(job.get("state") == "running" for job in _store().list(_NAMESPACE))


async def begin_exclusive(label: str) -> dict:
    """置 running 并返回快照；已有部署在跑抛 RuntimeError（API 层转 409）。"""
    global _JOB_ID, _SINK
    async with _JOB_LOCK:
        if running():
            raise RuntimeError("已有镜像部署任务在进行中")
        _SINK = None  # 防御：清掉上一次未触发的注册，避免串到本次部署
        _JOB_ID = uuid.uuid4().hex[:12]
        now = _now_ms()
        job = {
            **_IDLE_JOB,
            "id": _JOB_ID,
            "state": "running",
            "created_ms": now,
            "started_ms": now,
            "label": label,
            "log_tail": [f"部署目标：{label}"],
        }
        job = _copy_job(_store().create(_NAMESPACE, _JOB_ID, job))
        _publish_job("started", job)
        return job


def finish_noop(message: str) -> dict:
    """无需真正 deploy 时把任务置为 ok（如版本 pin 已一致）。"""
    global _JOB_ID
    now = _now_ms()
    _JOB_ID = uuid.uuid4().hex[:12]
    job = {
        **_IDLE_JOB,
        "id": _JOB_ID,
        "state": "ok",
        "created_ms": now,
        "started_ms": now,
        "finished_ms": now,
        "log_tail": [message],
    }
    job = _copy_job(_store().create(_NAMESPACE, _JOB_ID, job))
    _publish_job("finished", job)
    return job


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
    current = _current_job(create_if_missing=True)
    if current is None:  # pragma: no cover - create_if_missing 已保证
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *cli_argv("deploy", str(RUNNER_FILE)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=cli_env(resource_env()),
        )
    except (FileNotFoundError, OSError) as exc:
        if rollback:
            rollback()
        error = f"无法启动 modal CLI：{exc}"
        job = _current_job()
        if job is None:  # pragma: no cover
            return
        job.update(state="error", finished_ms=_now_ms(), error=error, log_tail=[])
        _publish_job("finished", _save_job(job))
        _fire_sink("rolled_back", error)
        return
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_DEPLOY_TIMEOUT)
    except TimeoutError:
        proc.kill()
        error = (
            f"modal deploy 超时（>{_DEPLOY_TIMEOUT:g}s）已终止；远端镜像可能仍在构建，"
            "新文件已保留，请先执行 `modal app list` 核对应用状态与部署时间，"
            "再查看 `modal app logs <app_name>` 和 `modal image logs <image_id>`，"
            "确认结果前不要重复发起 deploy"
        )
        job = _current_job()
        if job is None:  # pragma: no cover
            return
        job.update(
            state="error", finished_ms=_now_ms(),
            error=error,
            log_tail=["[超时终止]"],
        )
        _publish_job("finished", _save_job(job))
        _fire_sink("timeout", error)
        return
    tail = _output_tail(out, err)
    if proc.returncode != 0:
        if rollback:
            rollback()
        error = f"modal deploy 失败（退出码 {proc.returncode}），已回滚本次变更"
        job = _current_job()
        if job is None:  # pragma: no cover
            return
        job.update(
            state="error", finished_ms=_now_ms(),
            error=error,
            log_tail=tail,
        )
        _publish_job("finished", _save_job(job))
        _fire_sink("rolled_back", error)
        return
    job = _current_job()
    if job is None:  # pragma: no cover
        return
    job.update(state="ok", finished_ms=_now_ms(), error=None, log_tail=tail)
    _publish_job("finished", _save_job(job))
    _fire_sink("ok", None)


def spawn(rollback) -> None:
    """create_task 包装（保持任务引用防 GC）。"""
    global _TASK
    _TASK = asyncio.create_task(run_deploy(rollback))


def recover_interrupted() -> int:
    """启动恢复：运行态改为 interrupted，不自动重放或回滚远端变更。"""
    global _JOB_ID, _TASK
    changed = 0
    jobs = sorted(
        _store().list(_NAMESPACE),
        key=lambda job: float(job.get("created_ms") or 0),
    )
    for stored in jobs:
        if stored.get("state") != "running":
            continue
        job = _copy_job(stored)
        job.update(
            state="interrupted",
            finished_ms=_now_ms(),
            error=_INTERRUPTED_ERROR,
            log_tail=[*job.get("log_tail", []), "[重启中断] 远端结果不确定"][-_LOG_TAIL_LINES:],
        )
        _store().save(_NAMESPACE, str(job["id"]), job)
        changed += 1
    if jobs:
        latest = jobs[-1]
        latest_stored = _store().get(_NAMESPACE, str(latest.get("id") or ""))
        _JOB_ID = str(latest_stored.get("id")) if latest_stored else None
    _TASK = None
    return changed


def reset() -> None:
    """测试辅助：清空部署任务快照与进程内引用。"""
    global _JOB_ID, _TASK
    _store().clear(_NAMESPACE)
    _JOB_ID = None
    _TASK = None
    set_finish_sink(None)

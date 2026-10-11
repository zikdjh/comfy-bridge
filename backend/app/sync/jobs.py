"""同步任务注册表（M2-6 余项）：把长耗时同步操作任务化，前端统一 JobDrawer 跟踪。

设计（对齐前端界面规划 §5.2「长任务提交后立即返回 job_id → 自动打开 JobDrawer」）：
- 内存态注册表：job = {id, kind, label, state, total, done, items, logs, error, created_ms, finished_ms}
- runner 是 async 函数，接收 JobHandle（log/advance/check_cancel），注册表负责 spawn 与兜底异常
- 取消为协作式：runner 在条目间调用 check_cancel()，取消后 state=cancelled、已完成的条目保留
- 保留最近 MAX_JOBS 个作业（新作业挤出最老的已结束作业）；运行中作业永不清理
- 刷新以统一事件流为主、GET /api/v1/jobs 轮询为断线兜底（P1-5）

现状：工作流同步与节点同步接入本注册表；镜像重建/卷移动/URL 直下保持各自既有作业面不动。
"""

from __future__ import annotations

import asyncio
import time
import uuid

from ..core.job_store import default_store
from ..events import publish

MAX_JOBS = 100
_LOG_LIMIT = 400
_NAMESPACE = "sync"

_tasks: dict[str, asyncio.Task] = {}


def _store():
    return default_store()


def _all_jobs() -> list[dict]:
    return _store().list(_NAMESPACE)


def _job_view(job: dict) -> dict:
    return {
        k: (list(v) if isinstance(v, list) else v)
        for k, v in job.items()
        if k != "cancel_requested"
    }


def _emit_job(job: dict, action: str) -> None:
    """广播统一任务事件；节点同步额外保留 node_sync 领域事件名。"""
    view = _job_view(job)
    payload = {"action": action, "job": view}
    publish("job", payload)
    if job["kind"] == "node_sync":
        publish("node_sync", payload)


class JobCancelled(Exception):
    """runner 内 check_cancel() 抛出，标记任务为 cancelled。"""


class JobHandle:
    """runner 侧写入口：记日志、推进条目、检查取消。"""

    def __init__(self, job_id: str) -> None:
        self._id = job_id

    def log(self, message: str) -> None:
        job = _store().get(_NAMESPACE, self._id)
        if job is not None:
            job["logs"].append(f"{time.strftime('%H:%M:%S')} {message}")
            del job["logs"][:-_LOG_LIMIT]
            _store().save(_NAMESPACE, self._id, job)
            _emit_job(job, "log")

    def advance(self, item: dict) -> None:
        """推进一个条目（{name, ok, ...}）；item 原样入 items 列表供前端逐项展示。"""
        job = _store().get(_NAMESPACE, self._id)
        if job is None:
            return
        job["items"].append(item)
        job["done"] = len(job["items"])
        _store().save(_NAMESPACE, self._id, job)
        _emit_job(job, "advance")

    def check_cancel(self) -> None:
        job = _store().get(_NAMESPACE, self._id)
        if job is not None and job["cancel_requested"]:
            raise JobCancelled


def _prune() -> None:
    """超出容量时挤掉最老的已结束作业（运行中永不清理）。"""
    for job_id in _store().prune(
        _NAMESPACE,
        MAX_JOBS,
        is_running=lambda job: job["state"] == "running",
        created_at=lambda job: job["created_ms"],
    ):
        _tasks.pop(job_id, None)


def start_job(kind: str, label: str, total: int, runner, cancellable: bool = False) -> dict:
    """创建作业并 spawn runner；立即返回作业视图（调用方把它作为响应体）。"""
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "kind": kind,
        "label": label,
        "state": "running",
        "total": max(int(total), 0),
        "done": 0,
        "items": [],
        "logs": [],
        "error": None,
        "cancellable": bool(cancellable),
        "cancel_requested": False,
        "created_ms": time.time() * 1000,
        "finished_ms": None,
    }
    _store().create(_NAMESPACE, job_id, job)

    async def _run() -> None:
        handle = JobHandle(job_id)
        try:
            await runner(handle)
            if job["cancel_requested"]:
                job["state"] = "cancelled"
            elif job["state"] == "running":
                ok_items = sum(1 for i in job["items"] if i.get("ok"))
                failed = len(job["items"]) - ok_items
                job["state"] = "error" if failed or not job["items"] else "ok"
                if failed:
                    job["error"] = f"{failed} 个条目失败"
                elif not job["items"]:
                    job["error"] = "没有可执行的条目"
        except JobCancelled:
            job["state"] = "cancelled"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 —— 任务体兜底：任何异常都落到作业状态
            job["state"] = "error"
            job["error"] = str(exc) or type(exc).__name__
        finally:
            job["finished_ms"] = time.time() * 1000
            _store().save(_NAMESPACE, job_id, job)
            _emit_job(job, "finished")

    _tasks[job_id] = asyncio.get_running_loop().create_task(_run())
    _prune()
    _emit_job(job, "started")
    return _job_view(job)


def request_cancel(job_id: str) -> bool:
    """标记协作式取消；是否/何时真正停由 runner 的 check_cancel 决定。"""
    job = _store().get(_NAMESPACE, job_id)
    if job is None or job["state"] != "running" or not job["cancellable"]:
        return False
    job["cancel_requested"] = True
    job["logs"].append(f"{time.strftime('%H:%M:%S')} 已请求取消，当前条目完成后停止")
    _store().save(_NAMESPACE, job_id, job)
    _emit_job(job, "cancel_requested")
    return True


def get_job(job_id: str) -> dict | None:
    job = _store().get(_NAMESPACE, job_id)
    if job is None:
        return None
    return _job_view(job)


def list_jobs(limit: int = 50) -> list[dict]:
    rows = sorted(_all_jobs(), key=lambda j: j["created_ms"], reverse=True)[:limit]
    return [_job_view(j) for j in rows]


def running_count() -> int:
    return sum(1 for j in _all_jobs() if j["state"] == "running")


async def wait_job(job_id: str, timeout: float = 120.0) -> dict | None:
    """测试辅助：等待作业结束并返回最终视图（生产走轮询，不用这个）。"""
    task = _tasks.get(job_id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            pass
    return get_job(job_id)


def reset() -> None:
    """测试辅助：清空注册表。"""
    _store().clear(_NAMESPACE)
    _tasks.clear()

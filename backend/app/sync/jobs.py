"""同步任务注册表（M2-6 余项）：把长耗时同步操作任务化，前端统一 JobDrawer 跟踪。

设计（对齐前端界面规划 §5.2「长任务提交后立即返回 job_id → 自动打开 JobDrawer」）：
- 注册表：job = {id, kind, label, state, phase, total, done, items, logs, error,
  created_ms, started_ms, finished_ms}
- runner 是 async 函数，接收 JobHandle（log/advance/check_cancel），注册表负责 spawn 与兜底异常
- 取消为协作式：runner 在条目间调用 check_cancel()，取消后 state=cancelled、已完成的条目保留
- 保留最近 MAX_JOBS 个作业（新作业挤出最老的已结束作业）；运行中作业永不清理
- 刷新以统一事件流为主、GET /api/v1/jobs 轮询为断线兜底（P1-5）

现状：工作流同步与节点同步接入本注册表；生产环境的作业快照落 SQLite，
后端重启后历史仍可查询，遗留运行态统一收敛为 ``interrupted``。镜像重建/
卷移动/URL 直下保持各自既有作业面不动。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from ..core.job_store import SQLiteJobStore, default_store
from ..events import publish

MAX_JOBS = 100
_LOG_LIMIT = 400
_NAMESPACE = "sync"

_tasks: dict[str, asyncio.Task] = {}
MAX_JOBS = 100
_LOG_LIMIT = 400
_NAMESPACE = "sync"
_INTERRUPTED_ERROR = "桥接器重启，作业已中断；已完成条目保留，可重试未完成项"


def _store():
    store = default_store()
    if isinstance(store, SQLiteJobStore):
        store.set_terminal_predicate(_terminal, namespace=_NAMESPACE)
    return store


def _all_jobs() -> list[dict]:
    return _store().list(_NAMESPACE)


def _job_view(job: dict) -> dict:
    view = {
        k: (list(v) if isinstance(v, list) else v)
        for k, v in job.items()
        if k != "cancel_requested"
    }
    if job.get("state") == "interrupted":
        view["items"] = [*job.get("items", []), *retryable_items(str(job.get("id", "")))]
    return view


def _save(job_id: str, job: dict) -> dict:
    """保存并切回存储返回的工作副本（SQLite 模式是按值存储）。"""
    stored = _store().save(_NAMESPACE, job_id, job)
    # 兼容 P2-2 旧实现：外部存储允许 save 无返回值，此时工作对象仍在 caller 手里。
    return stored if isinstance(stored, dict) else job


def _running(job: dict) -> bool:
    return job.get("state") == "running"


def _terminal(job: dict) -> bool:
    return job.get("state") in {"ok", "error", "cancelled", "interrupted"}


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
            job = _save(self._id, job)
            _emit_job(job, "log")

    def phase(self, phase: str) -> None:
        """更新当前阶段；前端据此展示“正在做什么”，而不是只看进度数字。"""
        job = _store().get(_NAMESPACE, self._id)
        if job is None:
            return
        job["phase"] = str(phase).strip()[:80] or "处理中"
        job = _save(self._id, job)
        _emit_job(job, "phase")

    def advance(self, item: dict) -> None:
        """推进一个条目（{name, ok, ...}）；item 原样入 items 列表供前端逐项展示。"""
        job = _store().get(_NAMESPACE, self._id)
        if job is None:
            return
        job["items"].append(item)
        job["done"] = len(job["items"])
        job = _save(self._id, job)
        _emit_job(job, "advance")

    def check_cancel(self) -> None:
        job = _store().get(_NAMESPACE, self._id)
        if job is not None and job.get("cancel_requested"):
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
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "label": label,
        "state": "running",
        "phase": "准备中",
        "total": max(int(total), 0),
        "done": 0,
        "items": [],
        "logs": [],
        "error": None,
        "cancellable": bool(cancellable),
        "cancel_requested": False,
        "created_ms": time.time() * 1000,
        "started_ms": None,
        "finished_ms": None,
    }
    job = _store().create(_NAMESPACE, job_id, job)

    async def _run() -> None:
        nonlocal job
        handle = JobHandle(job_id)
        try:
            job = _store().get(_NAMESPACE, job_id)
            if job is None:
                return
            job["started_ms"] = time.time() * 1000
            job["phase"] = "执行中"
            job = _save(job_id, job)
            await runner(handle)
            # runner 可能经过多次按值保存；收尾必须从存储重新取最新快照。
            job = _store().get(_NAMESPACE, job_id)
            if job is None:
                return
            if job.get("cancel_requested"):
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
            job = _store().get(_NAMESPACE, job_id)
            if job is not None:
                job["state"] = "cancelled"
        except asyncio.CancelledError:
            job = _store().get(_NAMESPACE, job_id)
            if job is not None:
                job["state"] = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 —— 任务体兜底：任何异常都落到作业状态
            job = _store().get(_NAMESPACE, job_id)
            if job is not None:
                job["state"] = "error"
                job["error"] = str(exc) or type(exc).__name__
        finally:
            if job is not None:
                job["finished_ms"] = time.time() * 1000
                job["phase"] = {
                    "ok": "已完成",
                    "error": "执行失败",
                    "cancelled": "已取消",
                }.get(job["state"], job.get("phase") or "已结束")
                job = _save(job_id, job)
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
    job = _save(job_id, job)
    _emit_job(job, "cancel_requested")
    return True


def get_job(job_id: str) -> dict | None:
    job = _store().get(_NAMESPACE, job_id)
    if job is None:
        return None
    # API 详情由存储快照和恢复派生项组成；不要把它们回写到原始快照。
    return _job_view(job)


def retryable_items(job_id: str) -> list[dict]:
    """重启中断后的可重试项：已完成条目保留，未落项的余量显式标为中断。

    例如 2 项任务只有第 1 项落盘时，第 2 项原本没有名称。这里生成一条占位项，
    前端仅据此提示“请重新执行原操作”，不会自动重放或覆盖未知的云端状态。
    """
    job = _store().get(_NAMESPACE, job_id)
    if job is None or job.get("state") != "interrupted":
        return []
    rows = [
        {
            "name": str(item.get("name", "")),
            "ok": False,
            "action": item.get("action"),
            "error": item.get("error") or "重启中断，建议重新执行",
        }
        for item in job.get("items", [])
        if not item.get("ok")
    ]
    completed_without_name = sum(
        1 for item in job.get("items", [])
        if item.get("ok") and not item.get("name")
    )
    unfinished = max(int(job.get("total", 0)) - len(job.get("items", [])), 0)
    unresolved = max(unfinished, completed_without_name)
    if unresolved:
        rows.append({
            "name": f"未完成条目（{unresolved} 项）",
            "ok": False,
            "action": None,
            "error": "桥接器重启时状态无法确认，需重新发起原操作",
        })
    return rows


def list_jobs(limit: int = 50) -> list[dict]:
    rows = sorted(_all_jobs(), key=lambda j: j["created_ms"], reverse=True)[:limit]
    return [_job_view(j) for j in rows]


def running_count() -> int:
    return sum(1 for j in _all_jobs() if j["state"] == "running")


def recover_interrupted() -> int:
    """启动恢复：遗留运行态改为 interrupted，历史终态保持不变。

    同步作业不自动重放：上次是否已写入卷无法仅凭本地快照确认，自动重跑可能
    覆盖云端新文件。界面保留已完成条目，用户可按失败项显式重试。
    """
    jobs = _all_jobs()
    now_ms = time.time() * 1000
    changed = 0
    for job in jobs:
        if not _running(job):
            continue
        job["state"] = "interrupted"
        job["error"] = _INTERRUPTED_ERROR
        job["finished_ms"] = now_ms
        job["phase"] = "已中断（重启恢复）"
        job["cancel_requested"] = False
        _store().save(_NAMESPACE, job["id"], job)
        _tasks.pop(job["id"], None)
        changed += 1
    return changed


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
    for task in _tasks.values():
        if not task.done():
            task.cancel()
    _store().clear(_NAMESPACE)
    _tasks.clear()

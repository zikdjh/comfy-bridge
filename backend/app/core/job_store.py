"""作业状态存储接缝（P2-2）。

ComfyBridge 目前是单实例部署：作业状态放在进程内存里，后端一重启就清空。
本模块只抽象与持久化有关的最小能力——按命名空间创建/查询/列举/删除记录，
以及按"最旧的已结束记录"修剪——不接管各业务模块的字段结构与状态机。

默认实现 ``InMemoryJobStore`` 与历史行为一致（进程内、零外部依赖）；
未来多实例部署可替换成 Redis / PostgreSQL 实现，各业务模块不需要重写。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JobStore(Protocol):
    """键值型作业状态存储；``job_id`` 在命名空间内唯一。

    生命周期约定：``create`` / ``get`` 返回可变的作业工作对象（外部实现可在
    本地缓存工作副本），业务模块更新工作对象后调用 ``save``，由实现决定如何
    写入共享存储。``sync/jobs.py`` 与 ``modal_volume/jobs.py`` 已按此约定接入。
    """

    def create(self, namespace: str, job_id: str, job: Any) -> Any:
        ...

    def get(self, namespace: str, job_id: str) -> Any | None:
        ...

    def list(self, namespace: str) -> list[Any]:
        ...

    def save(self, namespace: str, job_id: str, job: Any) -> Any:
        """持久化一次变更并返回实现里的工作对象。

        内存实现保存引用；外部实现可按值序列化，并返回本地工作副本，供后续
        状态变更继续修改。业务模块应以返回值继续工作。
        """
        ...

    def delete(self, namespace: str, job_id: str) -> None:
        ...

    def prune(
        self,
        namespace: str,
        keep: int,
        *,
        is_running: Callable[[Any], bool],
        created_at: Callable[[Any], Any],
        include_running: bool = True,
    ) -> list[str]:
        """裁剪已结束记录，运行中的永不清理，返回被删 ID。

        ``include_running=True``（默认）：``keep`` 是记录总数上限
        （``sync/jobs`` 的 MAX_JOBS 语义，运行中占容量）。
        ``include_running=False``：``keep`` 是已完成记录数上限
        （卷同步/下载/移动的 finished-only 语义，运行中不占容量）。
        """
        ...

    def clear(self, namespace: str) -> None:
        ...


class InMemoryJobStore:
    """线程安全的进程内实现；语义与历史 ``dict`` 注册表一致。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}

    def create(self, namespace: str, job_id: str, job: Any) -> Any:
        with self._lock:
            self._data.setdefault(namespace, {})[job_id] = job
        return job

    def get(self, namespace: str, job_id: str) -> Any | None:
        with self._lock:
            return self._data.get(namespace, {}).get(job_id)

    def list(self, namespace: str) -> list[Any]:
        with self._lock:
            return list(self._data.get(namespace, {}).values())

    def save(self, namespace: str, job_id: str, job: Any) -> Any:
        with self._lock:
            self._data.setdefault(namespace, {})[job_id] = job
            return job

    def delete(self, namespace: str, job_id: str) -> None:
        with self._lock:
            self._data.get(namespace, {}).pop(job_id, None)

    def prune(
        self,
        namespace: str,
        keep: int,
        *,
        is_running: Callable[[Any], bool],
        created_at: Callable[[Any], Any],
        include_running: bool = True,
    ) -> list[str]:
        with self._lock:
            bucket = self._data.get(namespace, {})
            finished = [
                (job_id, job)
                for job_id, job in bucket.items()
                if not is_running(job)
            ]
            counted = len(bucket) if include_running else len(finished)
            overflow = counted - max(int(keep), 0)
            if overflow <= 0:
                return []
            removed: list[str] = [
                job_id
                for job_id, _ in sorted(
                    finished, key=lambda item: created_at(item[1])
                )[:overflow]
            ]
            for job_id in removed:
                self._data[namespace].pop(job_id, None)
            return removed

    def clear(self, namespace: str) -> None:
        with self._lock:
            self._data.pop(namespace, None)


_DEFAULT = InMemoryJobStore()


def default_store() -> JobStore:
    """返回进程默认存储；测试可用 ``set_default_store`` 替换。"""
    return _DEFAULT


def set_default_store(store: JobStore | None) -> JobStore:
    """替换默认存储并返回旧实例；``None`` 表示恢复进程内实现。"""
    global _DEFAULT
    previous = _DEFAULT
    _DEFAULT = store if store is not None else InMemoryJobStore()
    return previous

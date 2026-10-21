"""作业状态存储（P2-2 / 可靠性 R1）。

本模块只负责按命名空间创建/查询/列举/保存/删除记录，以及按最旧的已结束
记录修剪，不接管各业务模块的字段结构与状态机。默认进程内实现供测试和隔离
实例使用；生产启动时由 ``enable_sqlite`` 切换到 SQLite 快照，重启后历史作业
仍可查询。SQLite 模式每次变更都读写一份 JSON 快照，保持单实例语义，避免
长时间运行后内存快照与数据库分叉。
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

# 协议的 ``list`` 方法在类体内会遮蔽内建 ``list``，注解统一走别名避免 mypy 误解析
_JobIds = list[str]


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
    ) -> _JobIds:
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
    ) -> _JobIds:
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


_JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  namespace TEXT NOT NULL,
  job_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_ms REAL NOT NULL,
  updated_ms INTEGER NOT NULL,
  PRIMARY KEY (namespace, job_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_namespace_created
  ON jobs(namespace, created_ms DESC);
"""


class SQLiteJobStore:
    """SQLite JSON 快照存储；每条记录独立提交，适合单后端进程。

    终端状态只允许从运行态进入一次：``save`` 发现库里已经是终态时保留库内
    版本并返回它。这样重启前写了一半的协程无法用旧的工作副本覆盖已完成结果。
    """

    def __init__(
        self,
        db_path: str,
        *,
        is_terminal: Callable[[Any], bool] | None = None,
    ) -> None:
        self._db_path = db_path
        self._is_terminal = is_terminal or (lambda _job: False)
        self._terminal_by_namespace: dict[str, Callable[[Any], bool]] = {}
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def set_terminal_predicate(
        self,
        predicate: Callable[[Any], bool],
        *,
        namespace: str | None = None,
    ) -> None:
        """由业务模块提供终态判定，存储层不绑定具体状态字段。

        显式传入命名空间时只影响该业务，避免多个作业模块共用 SQLite 时互相
        覆盖终态判定；不传命名空间保留旧的全库默认语义。
        """
        if namespace is None:
            self._is_terminal = predicate
        else:
            self._terminal_by_namespace[namespace] = predicate

    def _terminal(self, namespace: str, job: Any) -> bool:
        predicate = self._terminal_by_namespace.get(namespace, self._is_terminal)
        return predicate(job)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_JOB_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    @staticmethod
    def _copy(job: Any) -> Any:
        return copy.deepcopy(job)

    def _decode(self, row: sqlite3.Row) -> Any:
        return json.loads(row["payload"])

    def create(self, namespace: str, job_id: str, job: Any) -> Any:
        with self._lock:
            conn = self._get_conn()
            payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
            created = float(getattr(job, "created_ms", 0) or 0)
            if not created and isinstance(job, dict):
                created = float(job.get("created_ms") or 0)
            conn.execute(
                """INSERT INTO jobs(namespace, job_id, payload, created_ms, updated_ms)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(namespace, job_id) DO UPDATE SET
                     payload=excluded.payload,
                     created_ms=excluded.created_ms,
                     updated_ms=excluded.updated_ms""",
                (namespace, job_id, payload, created, int(time.time() * 1000)),
            )
            conn.commit()
            return self._copy(job)

    def get(self, namespace: str, job_id: str) -> Any | None:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT payload FROM jobs WHERE namespace = ? AND job_id = ?",
                (namespace, job_id),
            ).fetchone()
            return self._decode(row) if row is not None else None

    def get_all(self, namespace: str) -> list[Any]:
        """内部批量读取；SQLite 类的方法名 ``list`` 不用于自身类型注解。"""
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT payload FROM jobs WHERE namespace = ? ORDER BY created_ms ASC",
                (namespace,),
            ).fetchall()
            return [self._decode(row) for row in rows]

    def list(self, namespace: str) -> list[Any]:
        return self.get_all(namespace)

    def save(self, namespace: str, job_id: str, job: Any) -> Any:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT payload FROM jobs WHERE namespace = ? AND job_id = ?",
                (namespace, job_id),
            ).fetchone()
            if row is not None:
                current = self._decode(row)
                # 终态是不可变墓碑：旧协程即使先 get 到运行态，也不能在恢复
                # 之后把已中断/已完成的快照重新推进或覆盖。
                if self._terminal(namespace, current):
                    return self._copy(current)
            payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """UPDATE jobs SET payload = ?, updated_ms = ?
                   WHERE namespace = ? AND job_id = ?""",
                (payload, int(time.time() * 1000), namespace, job_id),
            )
            conn.commit()
            return self._copy(job)

    def delete(self, namespace: str, job_id: str) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM jobs WHERE namespace = ? AND job_id = ?",
                (namespace, job_id),
            )
            conn.commit()

    def prune(
        self,
        namespace: str,
        keep: int,
        *,
        is_running: Callable[[Any], bool],
        created_at: Callable[[Any], Any],
        include_running: bool = True,
    ) -> _JobIds:
        with self._lock:
            jobs = self.list(namespace)
            finished: list[tuple[str, Any]] = []
            for job in jobs:
                job_id = str(getattr(job, "id", "") or (
                    job.get("id", "") if isinstance(job, dict) else ""
                ))
                if not is_running(job):
                    finished.append((job_id, job))
            counted = len(jobs) if include_running else len(finished)
            overflow = counted - max(int(keep), 0)
            if overflow <= 0:
                return []
            removed = [
                job_id
                for job_id, _ in sorted(
                    finished, key=lambda item: created_at(item[1])
                )[:overflow]
            ]
            conn = self._get_conn()
            conn.executemany(
                "DELETE FROM jobs WHERE namespace = ? AND job_id = ?",
                [(namespace, job_id) for job_id in removed],
            )
            conn.commit()
            return removed

    def clear(self, namespace: str) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM jobs WHERE namespace = ?", (namespace,))
            conn.commit()

    def interrupt_running(
        self,
        namespace: str,
        *,
        is_running: Callable[[Any], bool],
        error: str,
        finished_ms: int,
        phase: str,
    ) -> Any:
        """把所有运行态快照收敛为 ``interrupted``，返回更新后的工作对象。"""
        changed: list[Any] = []
        for job in self.get_all(namespace):
            if not is_running(job):
                continue
            if isinstance(job, dict):
                job["state"] = "interrupted"
                job["status"] = "interrupted"
                job["error"] = error
                job["finished_ms"] = finished_ms
                job["phase"] = phase
            else:
                for attr, value in (
                    ("state", "interrupted"),
                    ("status", "interrupted"),
                    ("error", error),
                    ("finished_ms", finished_ms),
                    ("phase", phase),
                ):
                    if hasattr(job, attr):
                        setattr(job, attr, value)
            changed.append(self.save(namespace, str(getattr(job, "id", "") or (
                job.get("id", "") if isinstance(job, dict) else ""
            )), job))
        return changed

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_DEFAULT: JobStore = InMemoryJobStore()


def default_store() -> JobStore:
    """返回进程默认存储；测试可用 ``set_default_store`` 替换。"""
    return _DEFAULT


def set_default_store(store: JobStore | None) -> JobStore:
    """替换默认存储并返回旧实例；``None`` 表示恢复进程内实现。"""
    global _DEFAULT
    previous = _DEFAULT
    _DEFAULT = store if store is not None else InMemoryJobStore()
    return previous


def enable_sqlite(db_path: str) -> SQLiteJobStore:
    """启动生产作业持久化；重复调用返回同一实例，不重复打开连接。"""
    global _DEFAULT
    if isinstance(_DEFAULT, SQLiteJobStore) and str(_DEFAULT._db_path) == str(db_path):
        return _DEFAULT
    if isinstance(_DEFAULT, SQLiteJobStore):
        _DEFAULT.close()
    store = SQLiteJobStore(str(db_path))
    _DEFAULT = store
    logging.getLogger(__name__).info("作业状态已持久化到 %s", db_path)
    return store


def close_default() -> None:
    """关闭默认 SQLite 连接；仅用于应用退出和测试收尾。"""
    global _DEFAULT
    if isinstance(_DEFAULT, SQLiteJobStore):
        _DEFAULT.close()
    _DEFAULT = InMemoryJobStore()

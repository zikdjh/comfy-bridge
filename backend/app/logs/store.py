"""请求日志落库（SQLite WAL）：每次 /prompt 拦截一条记录，图快照落盘 data/graphs/。

规划文档 §4 requests 表的最小子集（时间线/输出表随 Modal 执行通道一并接入）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GRAPH_DIR = DATA_DIR / "graphs"
DB_PATH = DATA_DIR / "bridge.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt_id TEXT UNIQUE,
  created_at TEXT NOT NULL,
  created_ms INTEGER NOT NULL,
  backend TEXT NOT NULL,
  final_backend TEXT NOT NULL,
  gen_type TEXT NOT NULL,
  workflow_name TEXT,
  graph_hash TEXT,
  graph_file TEXT,
  client_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  rule_trace TEXT,
  error TEXT,
  started_ms INTEGER,
  finished_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_ms DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        _conn = conn
    return _conn


def init_db() -> None:
    with _lock:
        _get_conn()


def close_stale_pending(note: str = "桥接重启，执行跟踪丢失") -> int:
    """启动时把遗留的 queued/running 记录关成 cancelled。

    执行跟踪（本地后台监视 / Modal 执行器）都在进程内存里，桥接一旦重启
    这些记录永远等不到终态，留着会把仪表盘“运行中任务”撑成僵尸列表。
    """
    with _lock:
        cur = _get_conn().execute(
            "UPDATE requests SET status = 'cancelled', error = ? WHERE status IN ('queued', 'running')",
            (note,),
        )
        _get_conn().commit()
        return cur.rowcount


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("rule_trace"):
        try:
            d["rule_trace"] = json.loads(d["rule_trace"])
        except (TypeError, ValueError):
            pass
    if d.get("started_ms") and d.get("finished_ms"):
        d["duration_ms"] = d["finished_ms"] - d["started_ms"]
    return d


def insert_request(record: dict) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO requests
               (prompt_id, created_at, created_ms, backend, final_backend, gen_type,
                workflow_name, graph_hash, graph_file, client_id, status, rule_trace, error)
               VALUES (:prompt_id, :created_at, :created_ms, :backend, :final_backend, :gen_type,
                       :workflow_name, :graph_hash, :graph_file, :client_id, :status, :rule_trace, :error)""",
            record,
        )
        conn.commit()
        return int(cur.lastrowid)


def update_status(prompt_id: str, status: str, error: str | None = None) -> None:
    """按状态机回写：queued → running → done/error/cancelled/blocked；终态后忽略后续事件。"""
    now_ms = int(time.time() * 1000)
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT status, started_ms FROM requests WHERE prompt_id = ?", (prompt_id,)
        ).fetchone()
        if row is None:
            return
        prev = row["status"]
        if prev in {"done", "error", "cancelled", "blocked"}:
            return
        started = row["started_ms"] or (now_ms if status == "running" else None)
        finished = now_ms if status in {"done", "error", "cancelled", "blocked"} else None
        conn.execute(
            """UPDATE requests
               SET status = ?, error = COALESCE(?, error), started_ms = ?, finished_ms = ?
               WHERE prompt_id = ?""",
            (status, error, started, finished, prompt_id),
        )
        conn.commit()


def list_requests(limit: int = 50, backend: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM requests"
    conds, params = [], []
    if backend and backend != "all":
        conds.append("backend = ?")
        params.append(backend)
    if status and status != "all":
        conds.append("status = ?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _lock:
        rows = _get_conn().execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_requests(backend: str | None = None, status: str | None = None) -> int:
    """过滤条件下的请求总数（/requests 的 total 用；list_requests 受 limit 截断）。"""
    sql = "SELECT COUNT(*) FROM requests"
    conds, params = [], []
    if backend and backend != "all":
        conds.append("backend = ?")
        params.append(backend)
    if status and status != "all":
        conds.append("status = ?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    with _lock:
        (n,) = _get_conn().execute(sql, params).fetchone()
    return n


def get_request(req_id: int) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_request_by_prompt(prompt_id: str) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM requests WHERE prompt_id = ?", (prompt_id,)).fetchone()
    return _row_to_dict(row) if row else None


GRAPH_KEEP = 500  # 图快照保留上限（按 mtime 淘汰最旧；请求记录查询缺失文件时按 404 兜底）


def _prune_graphs() -> None:
    try:
        files = list(GRAPH_DIR.glob("*.json"))
        if len(files) <= GRAPH_KEEP:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for p in files[: len(files) - GRAPH_KEEP]:
            p.unlink(missing_ok=True)
    except OSError:
        pass  # 清理失败不影响请求主流程


def save_graph_snapshot(prompt_id: str, raw_body: bytes) -> tuple[str, str]:
    """图快照落盘 data/graphs/<prompt_id>.json，返回 (相对文件名, sha256 前 16 位)。"""
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    graph_hash = hashlib.sha256(raw_body).hexdigest()[:16]
    name = f"{prompt_id}.json"
    (GRAPH_DIR / name).write_bytes(raw_body)
    _prune_graphs()
    return name, graph_hash


# —— async 封装：sqlite 同步 IO 放线程池，避免阻塞事件循环 ——

async def ainsert_request(record: dict) -> int:
    return await asyncio.to_thread(insert_request, record)


async def aupdate_status(prompt_id: str, status: str, error: str | None = None) -> None:
    await asyncio.to_thread(update_status, prompt_id, status, error)


async def asave_graph_snapshot(prompt_id: str, raw_body: bytes) -> tuple[str, str]:
    return await asyncio.to_thread(save_graph_snapshot, prompt_id, raw_body)


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

"""请求日志落库（SQLite WAL）：每次 /prompt 拦截一条记录，图快照落盘 data/graphs/。

规划文档 §4 requests 表的最小子集（时间线/输出表随 Modal 执行通道一并接入）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    """请求库目录；CB_DATA_DIR 用于测试和多实例隔离，默认保持安装态布局。"""
    override = os.environ.get("CB_DATA_DIR", "").strip()
    return Path(override) if override else Path(__file__).resolve().parents[2] / "data"


DATA_DIR = _data_dir()
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
  model_name TEXT,
  graph_hash TEXT,
  graph_file TEXT,
  client_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  rule_trace TEXT,
  error TEXT,
  modal_pid TEXT,
  started_ms INTEGER,
  finished_ms INTEGER,
  gpu_seconds REAL,
  est_cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_ms DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
"""

# 老库迁移：逐列补齐（ALTER ADD COLUMN 不支持 IF NOT EXISTS，先查 table_info）
_MIGRATE_COLUMNS = {
    "modal_pid": "ALTER TABLE requests ADD COLUMN modal_pid TEXT",
    "gpu_seconds": "ALTER TABLE requests ADD COLUMN gpu_seconds REAL",
    "est_cost_usd": "ALTER TABLE requests ADD COLUMN est_cost_usd REAL",
    "model_name": "ALTER TABLE requests ADD COLUMN model_name TEXT",
}

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
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(requests)")}
        for col, ddl in _MIGRATE_COLUMNS.items():
            if col not in existing:
                conn.execute(ddl)
        conn.commit()
        _conn = conn
    return _conn


def init_db() -> None:
    with _lock:
        _get_conn()


def close_stale_pending(note: str = "桥接重启，本地执行跟踪丢失") -> int:
    """启动时关闭本地侧遗留的 queued/running 记录。

    执行跟踪（本地后台监视 / Modal 执行器）都在进程内存里，桥接一旦重启本地
    请求永远等不到终态。云端（modal_pid 非空）请求仍在 Modal 上跑，真实结局
    交给 `pending_modal_reconcile()` 取出后由 reconcile 任务对账，这里不能关。
    终态统一补 finished_ms —— 真机缺陷 LV-06：旧实现直接 UPDATE，留下的
    cancelled 记录没有时长（bd506f9e，duration_ms 为空）。
    """
    with _lock:
        cur = _get_conn().execute(
            """UPDATE requests
               SET status = 'cancelled', error = ?, finished_ms = ?
               WHERE status IN ('queued', 'running')
                 AND (modal_pid IS NULL OR modal_pid = '')""",
            (note, int(time.time() * 1000)),
        )
        _get_conn().commit()
        return cur.rowcount


def pending_modal_reconcile() -> list[dict]:
    """重启后遗留的云端在跑记录（modal_pid 已持久化、状态未终态）。"""
    with _lock:
        rows = _get_conn().execute(
            """SELECT prompt_id, modal_pid, started_ms FROM requests
               WHERE status IN ('queued', 'running')
                 AND modal_pid IS NOT NULL AND modal_pid != ''
               ORDER BY created_ms DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def set_modal_pid(prompt_id: str, modal_pid: str) -> None:
    """云端受理后立即持久化映射（重启对账依赖它，不能只留在内存）。"""
    with _lock:
        _get_conn().execute(
            "UPDATE requests SET modal_pid = ? WHERE prompt_id = ?",
            (modal_pid, prompt_id),
        )
        _get_conn().commit()


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
        record = {**record, "modal_pid": record.get("modal_pid")}
        cur = conn.execute(
            """INSERT INTO requests
               (prompt_id, created_at, created_ms, backend, final_backend, gen_type,
                workflow_name, model_name, graph_hash, graph_file, client_id, status, rule_trace, error, modal_pid)
               VALUES (:prompt_id, :created_at, :created_ms, :backend, :final_backend, :gen_type,
                       :workflow_name, :model_name, :graph_hash, :graph_file, :client_id, :status, :rule_trace, :error, :modal_pid)""",
            record,
        )
        conn.commit()
        return int(cur.lastrowid or 0)


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


def record_cost(prompt_id: str, gpu_seconds: float, est_cost_usd: float) -> None:
    """成本按次入账（执行器终态时调用；与状态机解耦，终态前后均可写）。"""
    with _lock:
        _get_conn().execute(
            "UPDATE requests SET gpu_seconds = ?, est_cost_usd = ? WHERE prompt_id = ?",
            (gpu_seconds, est_cost_usd, prompt_id),
        )
        _get_conn().commit()


_MODAL_COST_WHERE = "final_backend = 'modal' AND est_cost_usd IS NOT NULL"


def cost_totals(start_ms: int | None = None, end_ms: int | None = None) -> dict:
    """区间内云端请求的成本聚合（当日/当月汇总共用；est_cost_usd 非空才算已入账）。"""
    sql = (
        "SELECT COUNT(*) AS requests, COALESCE(SUM(gpu_seconds), 0) AS gpu_seconds, "
        f"COALESCE(SUM(est_cost_usd), 0) AS est_cost_usd FROM requests WHERE {_MODAL_COST_WHERE}"
    )
    params: list = []
    if start_ms is not None:
        sql += " AND created_ms >= ?"
        params.append(start_ms)
    if end_ms is not None:
        sql += " AND created_ms < ?"
        params.append(end_ms)
    with _lock:
        row = _get_conn().execute(sql, params).fetchone()
    return {
        "requests": int(row["requests"]),
        "gpu_seconds": round(float(row["gpu_seconds"]), 1),
        "est_cost_usd": round(float(row["est_cost_usd"]), 5),
    }


def cost_daily(days: int = 30) -> list[dict]:
    """近 N 天逐日成本聚合（本地时区日界；无用量的日子不出现在结果里，由前端补零）。"""
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    sql = (
        "SELECT substr(created_at, 1, 10) AS date, COUNT(*) AS requests, "
        "COALESCE(SUM(gpu_seconds), 0) AS gpu_seconds, COALESCE(SUM(est_cost_usd), 0) AS est_cost_usd "
        f"FROM requests WHERE {_MODAL_COST_WHERE} AND created_ms >= ? "
        "GROUP BY date ORDER BY date"
    )
    with _lock:
        rows = _get_conn().execute(sql, (cutoff_ms,)).fetchall()
    return [
        {
            "date": r["date"],
            "requests": int(r["requests"]),
            "gpu_seconds": round(float(r["gpu_seconds"]), 1),
            "est_cost_usd": round(float(r["est_cost_usd"]), 5),
        }
        for r in rows
    ]


_COST_BREAKDOWN_KEYS = {
    "workflow": "COALESCE(NULLIF(workflow_name, ''), '（未命名）')",
    "model": "COALESCE(NULLIF(model_name, ''), '（未记录）')",
}


def cost_breakdown(by: str, days: int = 30, limit: int = 50) -> list[dict]:
    """成本按维度聚合（报表 v2）：by ∈ {workflow, model}，按估算金额降序。"""
    key_expr = _COST_BREAKDOWN_KEYS.get(by)
    if key_expr is None:
        raise ValueError(f"未知聚合维度: {by}")
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    sql = (
        f"SELECT {key_expr} AS key, COUNT(*) AS requests, "
        "COALESCE(SUM(gpu_seconds), 0) AS gpu_seconds, COALESCE(SUM(est_cost_usd), 0) AS est_cost_usd "
        f"FROM requests WHERE {_MODAL_COST_WHERE} AND created_ms >= ? "
        "GROUP BY key ORDER BY est_cost_usd DESC LIMIT ?"
    )
    with _lock:
        rows = _get_conn().execute(sql, (cutoff_ms, limit)).fetchall()
    return [
        {
            "key": r["key"],
            "requests": int(r["requests"]),
            "gpu_seconds": round(float(r["gpu_seconds"]), 1),
            "est_cost_usd": round(float(r["est_cost_usd"]), 5),
        }
        for r in rows
    ]


def cost_entries(days: int = 30, limit: int = 20000) -> list[dict]:
    """逐条成本流水（CSV 导出用）：仅云端已入账请求，新→旧。"""
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    sql = (
        "SELECT created_at, workflow_name, model_name, gen_type, final_backend, status, "
        "gpu_seconds, est_cost_usd, error FROM requests "
        f"WHERE {_MODAL_COST_WHERE} AND created_ms >= ? ORDER BY id DESC LIMIT ?"
    )
    with _lock:
        rows = _get_conn().execute(sql, (cutoff_ms, limit)).fetchall()
    return [dict(r) for r in rows]


def cost_daily_utc(start_ms: int, end_ms: int) -> list[dict]:
    """区间内逐日成本（UTC 日界，与 Modal 账单 report 的日粒度对齐，对账专用）。"""
    sql = (
        "SELECT date(created_ms / 1000, 'unixepoch') AS date, COUNT(*) AS requests, "
        "COALESCE(SUM(gpu_seconds), 0) AS gpu_seconds, COALESCE(SUM(est_cost_usd), 0) AS est_cost_usd "
        f"FROM requests WHERE {_MODAL_COST_WHERE} AND created_ms >= ? AND created_ms < ? "
        "GROUP BY date ORDER BY date"
    )
    with _lock:
        rows = _get_conn().execute(sql, (start_ms, end_ms)).fetchall()
    return [
        {
            "date": r["date"],
            "requests": int(r["requests"]),
            "gpu_seconds": round(float(r["gpu_seconds"]), 1),
            "est_cost_usd": round(float(r["est_cost_usd"]), 5),
        }
        for r in rows
    ]


def workflow_run_stats(days: int = 30, limit: int = 100) -> list[dict]:
    """按工作流聚合运行统计（requests 表现算，遵循成本口径 ADR：不做回写表/夜间任务）。

    duration_ms 只在终态请求上有值（读取时由 finished_ms - started_ms 现算，非表列），
    AVG/MAX 聚合自动忽略 NULL；估算成本只累计云端入账请求（final_backend = 'modal'），
    本地执行恒为 0 成本。未命名工作流（NULL/空串）落「（未命名）」桶，与成本报表同口径。
    """
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    duration = "CASE WHEN finished_ms IS NOT NULL AND started_ms IS NOT NULL THEN finished_ms - started_ms END"
    sql = (
        "SELECT COALESCE(NULLIF(workflow_name, ''), '（未命名）') AS name, "
        "COUNT(*) AS runs, "
        "SUM(status = 'done') AS done, "
        "SUM(status = 'error') AS errored, "
        "SUM(final_backend = 'modal') AS modal_runs, "
        f"AVG({duration}) AS avg_ms, "
        f"MAX({duration}) AS max_ms, "
        "COALESCE(SUM(CASE WHEN final_backend = 'modal' THEN est_cost_usd ELSE 0 END), 0) AS est_cost_usd, "
        "MAX(created_ms) AS last_ms "
        "FROM requests WHERE created_ms >= ? "
        "GROUP BY name ORDER BY runs DESC LIMIT ?"
    )
    with _lock:
        rows = _get_conn().execute(sql, (cutoff_ms, limit)).fetchall()
    return [
        {
            "name": r["name"],
            "runs": int(r["runs"]),
            "done": int(r["done"]),
            "errored": int(r["errored"]),
            "modal_runs": int(r["modal_runs"]),
            "avg_ms": round(float(r["avg_ms"])) if r["avg_ms"] is not None else None,
            "max_ms": int(r["max_ms"]) if r["max_ms"] is not None else None,
            "est_cost_usd": round(float(r["est_cost_usd"]), 5),
            "last_ms": int(r["last_ms"]),
        }
        for r in rows
    ]


def requests_missing_model(limit: int = 2000) -> list[dict]:
    """无模型字段但有图快照的请求（启动回填用）：只取 id + graph_file。"""
    with _lock:
        rows = _get_conn().execute(
            "SELECT id, graph_file FROM requests "
            "WHERE model_name IS NULL AND graph_file IS NOT NULL LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_model_name(req_id: int, model_name: str | None) -> None:
    with _lock:
        _get_conn().execute(
            "UPDATE requests SET model_name = ? WHERE id = ?", (model_name, req_id)
        )
        _get_conn().commit()


def list_requests(limit: int = 50, backend: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM requests"
    conds: list[str] = []
    params: list[Any] = []
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
    conds: list[str] = []
    params: list[Any] = []
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


def count_active_modal() -> int:
    """在途云端任务数：final_backend=modal 且尚未终态（queued/running）——并发护栏计数源。

    用 final_backend 而非 backend：护栏/可用性回退会把 final 改成本地，这类请求不占云端槽位。
    保守护栏：决策（计数）与入队（落库）之间无事务，±1 竞态可容忍。
    """
    with _lock:
        (n,) = _get_conn().execute(
            "SELECT COUNT(*) FROM requests WHERE final_backend = 'modal' AND status IN ('queued', 'running')"
        ).fetchone()
    return n


def list_active() -> list[dict]:
    """在途任务（queued/running，不分端）：/queue 合成与队列面板数据源。"""
    with _lock:
        rows = _get_conn().execute(
            "SELECT prompt_id, client_id, status, workflow_name, model_name, final_backend "
            "FROM requests WHERE status IN ('queued', 'running') ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


async def alist_active() -> list[dict]:
    return await asyncio.to_thread(list_active)


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


async def acount_active_modal() -> int:
    return await asyncio.to_thread(count_active_modal)


async def aupdate_status(prompt_id: str, status: str, error: str | None = None) -> None:
    await asyncio.to_thread(update_status, prompt_id, status, error)


async def areconcile_finish(
    prompt_id: str, *, status: str, error: str | None = None, started_ms: int | None
) -> None:
    """重启对账回写：绕开状态机幂等锁（记录已被启动清理关单）。

    正常执行走 update_status（终态后忽略后续事件）；对账发生在启动清理之后，
    记录已是 cancelled，必须强制改写为云端真实结局并补全时间线。
    """
    def _do() -> None:
        _get_conn().execute(
            """UPDATE requests
               SET status = ?, error = COALESCE(?, error), started_ms = COALESCE(started_ms, ?),
                   finished_ms = ?
               WHERE prompt_id = ?""",
            (status, error, started_ms, int(time.time() * 1000), prompt_id),
        )
        _get_conn().commit()

    await asyncio.to_thread(_do)


async def aset_modal_pid(prompt_id: str, modal_pid: str) -> None:
    await asyncio.to_thread(set_modal_pid, prompt_id, modal_pid)


async def arecord_cost(prompt_id: str, gpu_seconds: float, est_cost_usd: float) -> None:
    await asyncio.to_thread(record_cost, prompt_id, gpu_seconds, est_cost_usd)


async def asave_graph_snapshot(prompt_id: str, raw_body: bytes) -> tuple[str, str]:
    return await asyncio.to_thread(save_graph_snapshot, prompt_id, raw_body)


async def acost_breakdown(by: str, days: int = 30, limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(cost_breakdown, by, days, limit)


async def acost_entries(days: int = 30, limit: int = 20000) -> list[dict]:
    return await asyncio.to_thread(cost_entries, days, limit)


async def acost_totals(start_ms: int | None = None, end_ms: int | None = None) -> dict:
    return await asyncio.to_thread(cost_totals, start_ms, end_ms)


async def acost_daily_utc(start_ms: int, end_ms: int) -> list[dict]:
    return await asyncio.to_thread(cost_daily_utc, start_ms, end_ms)


async def aworkflow_run_stats(days: int = 30, limit: int = 100) -> list[dict]:
    return await asyncio.to_thread(workflow_run_stats, days, limit)


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    # astimezone()：拿到本地墙钟时间并附 tzinfo，规避 DTZ005 又不改变落库格式
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

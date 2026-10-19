"""工作流 API：本地清单 + 双向同步（M2-4）+ 冲突对比。

- GET  /workflows/local         本地工作流检测（user/default/workflows/ 只读探测）
- GET  /workflows/sync          同步矩阵：本地⇄数据卷 user/default/workflows/ 三方检测
                                （last-synced 基线 hash，冲突/变更分类，见 sync/workflows_rules）
- POST /workflows/sync          执行动作 push/pull/keep_both（keep_both = 冲突双副本）
- GET  /workflows/run-stats     运行统计：requests 表按工作流聚合（次数/成败/本地云端/
                                平均与最大耗时/估算成本/最近运行），SQL 现算无回写表
- GET  /workflows/compare       冲突双列对比：本地版 vs 云端版元数据 + 内容摘要
                                （节点类型计数 / 模型引用，UI 格式与 API 格式都能解析）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.config import get_settings, workflows_dir
from ...core.constants import MODEL_FILE_EXTS
from ...logs import store
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError
from ...sync import workflows_sync
from ...sync.workflows_rules import valid_workflow_name

router = APIRouter()


class SyncActionsBody(BaseModel):
    actions: list[dict]


def _short_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _probe_assets_db() -> dict | None:
    db_path = Path(get_settings().local.comfy_root) / "user" / "comfyui.db"
    if not db_path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assets = (
                con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                if "assets" in tables
                else None
            )
            return {"path": str(db_path), "tables": sorted(tables), "assets_count": assets}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"path": str(db_path), "error": str(exc)}


@router.get("/workflows/local")
async def workflows_local() -> dict:
    wf_dir = workflows_dir()
    items: list[dict] = []
    if wf_dir.is_dir():
        for p in sorted(wf_dir.glob("*.json")):
            stat = p.stat()
            items.append(
                {
                    "name": p.stem,
                    "file": p.name,
                    "size_bytes": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    "hash": _short_hash(p),
                }
            )
    return {
        "workflows_dir": str(wf_dir),
        "dir_exists": wf_dir.is_dir(),
        "workflows": items,
        "assets_db": _probe_assets_db(),
    }


@router.get("/workflows/sync")
async def workflows_sync_status() -> dict:
    """本地 ⇄ 云端数据卷 工作流同步矩阵（三方检测，读取时顺带刷新基线）。"""
    return await workflows_sync.status()


@router.post("/workflows/sync")
async def workflows_sync_apply(body: SyncActionsBody) -> dict:
    """执行同步动作 [{name, action}]（action ∈ push|pull|keep_both）——M2-6 任务化：
    预检失败返回 {ok: false, error}；否则立即返回 job，进度经 GET /jobs 轮询。"""
    return await workflows_sync.apply_as_job(body.actions)


@router.get("/workflows/run-stats")
async def workflows_run_stats(days: int = 30, limit: int = 50) -> dict:
    """运行统计（规划 §8.3）：按工作流聚合 requests 表——次数/成败/本地云端拆分/
    平均与最大耗时/估算成本/最近运行。SQL 现算，无独立回写表。"""
    if not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="days 需在 1~365 之间")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 需在 1~200 之间")
    rows = await store.aworkflow_run_stats(days, limit)
    for r in rows:
        r["local_runs"] = r["runs"] - r["modal_runs"]
    return {
        "days": days,
        "rows": rows,
        "note": "统计自桥接请求日志（requests 表）实时聚合；估算成本只计云端执行请求",
    }


def _summarize_workflow(data) -> dict:
    """工作流 JSON → 内容摘要：节点类型计数 + 模型引用（UI 格式 / API 格式通吃）。"""
    types: dict[str, int] = {}
    models: set[str] = set()

    def _note_model(val) -> None:
        if isinstance(val, str) and Path(val).suffix.lower() in MODEL_FILE_EXTS:
            models.add(val)

    nodes = []
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        # UI 导出格式：{nodes: [{type, widgets_values…}], links…}
        for n in data["nodes"]:
            if isinstance(n, dict):
                nodes.append(str(n.get("type") or "?"))
                for w in n.get("widgets_values") or []:
                    if isinstance(w, str):
                        _note_model(w)
                    elif isinstance(w, list):
                        for sub in w:
                            if isinstance(sub, str):
                                _note_model(sub)
    elif isinstance(data, dict):
        # API 格式：{id: {class_type, inputs…}}
        for n in data.values():
            if isinstance(n, dict):
                nodes.append(str(n.get("class_type") or "?"))
                for v in (n.get("inputs") or {}).values():
                    if isinstance(v, str):
                        _note_model(v)
    for t in nodes:
        types[t] = types.get(t, 0) + 1
    return {
        "ok": True,
        "node_count": len(nodes),
        "types": sorted(types.items(), key=lambda kv: -kv[1])[:24],
        "models": sorted(models),
    }


def _side_meta(path: Path, with_mtime: bool) -> dict:
    meta: dict = {"exists": path.is_file()}
    if not meta["exists"]:
        return meta
    try:
        stat = path.stat()
        meta["size_bytes"] = stat.st_size
        if with_mtime:
            meta["mtime"] = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    except OSError:
        pass
    try:
        meta["hash"] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        meta.update(_summarize_workflow(json.loads(path.read_text(encoding="utf-8", errors="replace"))))
    except (OSError, ValueError):
        meta["summary_error"] = "内容读取或 JSON 解析失败"
    return meta


@router.get("/workflows/compare")
async def workflows_compare(name: str) -> dict:
    """冲突双列对比数据（name 为文件名）；云端版经 SDK 网关现读（mtime 不可得，略）。"""
    if not valid_workflow_name(name):
        raise HTTPException(status_code=422, detail="非法工作流文件名")
    root = workflows_dir()
    local = await asyncio.to_thread(_side_meta, root / name, True)
    cloud: dict = {"exists": False}
    cloud_missing = False
    modal_configured = workflows_sync.modal_configured()
    if modal_configured:
        tmp_root = Path(tempfile.mkdtemp(prefix="cb-wf-cmp-"))
        try:
            dest = tmp_root / name
            await volume_cli.get_file(f"{workflows_sync.CLOUD_DIR}/{name}", dest, force=True)
            cloud = await asyncio.to_thread(_side_meta, dest, False)
        except ModalCliError as exc:
            detail = str(exc)
            cloud_missing = "No such file or directory" in detail
            cloud = {"exists": False, "error": detail[:300]}
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
    else:
        cloud = {"exists": False, "error": "未配置 Modal 凭证"}
    if not local.get("exists") and not cloud.get("exists") and cloud_missing:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {name}")
    return {
        "name": name,
        "local": local,
        "cloud": cloud,
        "identical": bool(local.get("hash") and local.get("hash") == cloud.get("hash")),
    }

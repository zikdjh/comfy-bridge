"""GET /api/v1/workflows/local —— 本地工作流检测。

Desktop 版工作流以 JSON 文件保存在 user/default/workflows/；
SQLite(comfyui.db) 是资产(assets)系统，不含工作流，此处只读探测并报告。
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

from ...core.config import get_settings, workflows_dir

router = APIRouter()


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
                    "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "hash": _short_hash(p),
                }
            )
    return {
        "workflows_dir": str(wf_dir),
        "dir_exists": wf_dir.is_dir(),
        "workflows": items,
        "assets_db": _probe_assets_db(),
    }

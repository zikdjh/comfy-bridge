"""模型卷同步 API：本地模型目录 ↔ Modal 数据卷（comfyui-data）的对比与同步。

- GET  /api/v1/modal-volume            元信息：卷名 / 分类 / 凭证与 CLI 可用性
- GET  /api/v1/modal-volume/overview   指定分类的本地+云端文件并集对比
- POST /api/v1/modal-volume/sync       创建同步作业（push=上传 / pull=下载）
- GET  /api/v1/modal-volume/jobs       同步作业列表（内存态）
- POST /api/v1/modal-volume/jobs/{id}/cancel
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.config import modal_configured, model_dirs_resolved
from ...modal_volume import client, jobs
from ...modal_volume.client import ModalCliError

router = APIRouter(prefix="/modal-volume", tags=["modal-volume"])

_MAX_LISTED_FILES = 3000


def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} TB"


@router.get("")
async def meta() -> dict:
    return {
        "volume": client.volume_name(),
        "credentials_ok": modal_configured(),
        "cli_found": shutil.which("modal") is not None,
        "categories": sorted(model_dirs_resolved()),
    }


@router.get("/overview")
async def overview(category: str = Query("checkpoints")) -> dict:
    resolved = model_dirs_resolved()
    if category not in resolved:
        raise HTTPException(status_code=404, detail=f"未知模型分类: {category}")

    if not modal_configured():
        return {"ok": False, "reason": "未配置 Modal 凭证（设置 → Modal）", "category": category, "rows": []}
    if shutil.which("modal") is None:
        return {"ok": False, "reason": "本机未找到 modal CLI", "category": category, "rows": []}

    local_dir = Path(resolved[category])
    local_files: dict[str, str] = {}
    if local_dir.is_dir():
        for path in sorted(local_dir.rglob("*")):
            if len(local_files) >= _MAX_LISTED_FILES:
                break
            if path.is_file() and not path.name.startswith("."):
                rel = path.relative_to(local_dir).as_posix()
                local_files[rel] = _human(path.stat().st_size)

    cloud_files: dict[str, str] = {}
    cloud_error: str | None = None
    try:
        for entry in await client.list_files(f"models/{category}"):
            if len(cloud_files) >= _MAX_LISTED_FILES:
                break
            cloud_files[entry["name"]] = entry["size"]
    except ModalCliError as exc:
        cloud_error = str(exc)

    rows = []
    for name in sorted(set(local_files) | set(cloud_files)):
        has_local, has_cloud = name in local_files, name in cloud_files
        rows.append({
            "name": name,
            "status": "both" if has_local and has_cloud else ("only_local" if has_local else "only_cloud"),
            "local_size": local_files.get(name),
            "cloud_size": cloud_files.get(name),
        })

    return {
        "ok": cloud_error is None,
        "cloud_error": cloud_error,
        "category": category,
        "local_dir": str(local_dir),
        "volume": client.volume_name(),
        "local_count": len(local_files),
        "cloud_count": len(cloud_files),
        "rows": rows,
    }


class SyncBody(BaseModel):
    direction: str  # push | pull
    category: str
    names: list[str]


@router.post("/sync")
async def sync(body: SyncBody) -> dict:
    try:
        job = jobs.create_job(body.direction, body.category, body.names)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"job": job.to_dict()}


@router.get("/jobs")
async def job_list() -> dict:
    return {"jobs": [job.to_dict() for job in jobs.list_jobs()]}


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str) -> dict:
    ok = await jobs.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="作业不存在或已结束")
    return {"cancelled": True}

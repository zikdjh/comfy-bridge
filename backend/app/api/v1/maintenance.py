"""用户数据备份、恢复与保留策略接口。"""

from __future__ import annotations

import sqlite3
import uuid
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...core import audit, maintenance, release_state

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class BackupBody(BaseModel):
    reason: str = "manual"


class RetentionBody(BaseModel):
    days: int


@router.get("")
async def maintenance_status() -> dict:
    try:
        return {**maintenance.status(), "release": release_state.status()}
    except (OSError, sqlite3.DatabaseError) as exc:
        raise HTTPException(status_code=503, detail=maintenance.error_guidance(exc)) from exc


@router.post("/backup")
async def create_backup(body: BackupBody | None = None) -> dict:
    reason = (body.reason if body else "manual").strip() or "manual"
    try:
        return maintenance.create_backup(reason=reason)
    except OSError as exc:
        raise HTTPException(status_code=507, detail=maintenance.error_guidance(exc)) from exc
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=409, detail=maintenance.error_guidance(exc)) from exc


@router.get("/backups/{name}")
async def download_backup(name: str) -> FileResponse:
    try:
        path = maintenance.resolve_backup(name)
    except maintenance.BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="备份不存在") from exc
    audit.record({"event": "backup_download", "name": path.name})
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/zip",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/restore")
async def restore_backup(request: Request) -> dict:
    """接收原始 zip 请求体，避免引入 multipart 解析依赖。"""
    home = maintenance.config_dir()
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / f".restore-upload-{uuid.uuid4().hex}.zip"
    written = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="恢复文件超过 2 GiB 限制")
                out.write(chunk)
        if written == 0:
            raise HTTPException(status_code=422, detail="恢复请求体为空")
        if not zipfile.is_zipfile(tmp):
            raise HTTPException(status_code=422, detail="恢复请求体不是 zip 备份")
        result = maintenance.stage_restore(tmp)
    except maintenance.RestoreBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except maintenance.BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=maintenance.error_guidance(exc)) from exc
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=409, detail=maintenance.error_guidance(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    audit.record({"event": "restore_staged", "name": result["name"]})
    return result


@router.put("/retention")
async def update_retention(body: RetentionBody) -> dict:
    try:
        result = maintenance.save_retention(body.days)
    except maintenance.BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record({"event": "retention_update", "days": result["days"]})
    return result


@router.post("/cleanup")
async def cleanup_now() -> dict:
    try:
        result = maintenance.cleanup_retention()
    except maintenance.BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=409, detail=maintenance.error_guidance(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=maintenance.error_guidance(exc)) from exc
    audit.record({"event": "retention_cleanup", **result})
    return result

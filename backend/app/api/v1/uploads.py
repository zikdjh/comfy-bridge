"""模型分块上传 API：会话、断点查询、分块写入、收尾与清理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...models_mgr import uploader

router = APIRouter(prefix="/uploads", tags=["uploads"])

_MAX_CHUNK_BYTES = 16 * 1024 * 1024


class InitUploadBody(BaseModel):
    model_config = {"extra": "forbid"}

    filename: str
    size: int
    md5: str
    category: str | None = None
    dest_dir: str | None = None
    volume: str | None = None


@router.post("/init")
async def init_upload(body: InitUploadBody) -> dict:
    try:
        session = await uploader.create_upload(
            filename=body.filename,
            size=body.size,
            md5=body.md5,
            category=body.category,
            dest_dir=body.dest_dir,
            volume=body.volume,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"upload": session}


@router.get("")
async def upload_list(limit: int = 50) -> dict:
    return {"uploads": uploader.list_uploads(limit=max(1, min(limit, 200)))}


@router.post("/prune")
async def prune_uploads(older_than_ms: int = 0) -> dict:
    """清理尚未开始写块的会话；有进度的断点上传不会被删除。"""
    if older_than_ms < 0:
        raise HTTPException(status_code=422, detail="older_than_ms 不能为负")
    return {"deleted": uploader.prune_not_started(older_than_ms=older_than_ms)}


@router.get("/{upload_id}")
async def upload_status(upload_id: str) -> dict:
    try:
        return {"upload": await uploader.get_upload(upload_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/{upload_id}/chunks/{index}")
async def put_chunk(upload_id: str, index: int, request: Request) -> dict:
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > _MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail=f"分块超过上限 {_MAX_CHUNK_BYTES} 字节")
    data = await request.body()
    if len(data) > _MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail=f"分块超过上限 {_MAX_CHUNK_BYTES} 字节")
    try:
        session = await uploader.write_chunk(
            upload_id,
            index,
            data,
            request.headers.get("x-chunk-md5", ""),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"upload": session}


@router.post("/{upload_id}/complete")
async def complete_upload(upload_id: str) -> dict:
    try:
        return {"upload": await uploader.complete_upload(upload_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{upload_id}")
async def delete_upload(upload_id: str) -> dict:
    try:
        deleted = await uploader.delete_upload(upload_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="上传会话不存在")
    return {"deleted": True}

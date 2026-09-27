"""GET /api/v1/media/local —— 本地产出媒体（图片/视频）浏览。

数据源：配置的 local.output_dir（ComfyUI output 目录，M1 后远端回传结果也落在这里）。
文件本体经 /media/local/file 下发，path 参数限制在 output_dir 内以防目录穿越。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ...core.config import get_settings

router = APIRouter(prefix="/media", tags=["media"])

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def _output_root():
    return Path(get_settings().local.output_dir)


@router.get("/local")
def list_local_media(
    kind: str = Query("all", pattern="^(all|image|video)$"),
    limit: int = Query(500, ge=1, le=1000),
) -> dict:
    root = _output_root()
    items: list[dict] = []
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext in IMAGE_EXTS:
                    file_kind = "image"
                elif ext in VIDEO_EXTS:
                    file_kind = "video"
                else:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                items.append(
                    {
                        "path": os.path.relpath(full, root).replace("\\", "/"),
                        "kind": file_kind,
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                    }
                )
    items.sort(key=lambda x: -x["mtime"])
    if kind != "all":
        items = [i for i in items if i["kind"] == kind]
    return {"root": str(root), "total": len(items), "items": items[:limit]}


@router.get("/local/file")
def get_local_media_file(path: str) -> FileResponse:
    root = _output_root().resolve()
    target = (root / path).resolve()
    if target != root and not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="path 越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target)

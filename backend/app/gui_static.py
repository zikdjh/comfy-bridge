"""管理面 GUI 静态托管（/bridge 前缀，M4 打包分发）。

前端构建产物由 `pnpm build:embed` 输出到 app/static/（vite --base=/bridge/），
本模块在产物存在时把控制台挂到 /bridge：/bridge/assets/* 走静态文件，
其余 /bridge/* 一律回退 index.html（SPA 客户端路由）。
/ 与未匹配路径仍是 ComfyUI 数据面透传，管理面不得侵占数据面根路径。
仓库开发通常没有产物目录（GUI 走 Vite :15173），此时跳过挂载。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def mount_gui(app: FastAPI, static_dir: Path | None = None) -> bool:
    """产物存在时挂载 /bridge，返回是否挂载。必须在 catch-all 透传路由之前调用。"""
    root = (static_dir or _STATIC_DIR).resolve()
    index = root / "index.html"
    if not index.is_file():
        return False

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/bridge/assets", StaticFiles(directory=assets), name="gui-assets")

    @app.get("/bridge", include_in_schema=False)
    @app.get("/bridge/{rest:path}", include_in_schema=False)
    async def gui_fallback(rest: str = "") -> FileResponse:
        candidate = (root / rest).resolve()
        # 只回源产物目录内的真实文件（防路径穿越），其余一律给 SPA 入口
        if rest and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)

    return True

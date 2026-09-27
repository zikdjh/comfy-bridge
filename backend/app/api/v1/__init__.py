"""管理面 API（/api/v1）。数据面（ComfyUI 原生路径代理）在 app/proxy 模块实现。"""

from fastapi import APIRouter

from . import config, health, launch, logs, media, modal, modal_volume, nodes, requests, routing, volume, workflows

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(launch.router)
api_router.include_router(nodes.router)
api_router.include_router(workflows.router)
api_router.include_router(media.router)
api_router.include_router(logs.router)
api_router.include_router(requests.router)
api_router.include_router(routing.router)
api_router.include_router(modal_volume.router)
api_router.include_router(volume.router)
api_router.include_router(modal.router)

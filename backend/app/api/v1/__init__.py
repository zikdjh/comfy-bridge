"""管理面 API（/api/v1）。数据面（ComfyUI 原生路径代理）在 app/proxy 模块实现。"""

from fastapi import APIRouter

from . import (
    config,
    cost,
    events,
    health,
    jobs,
    launch,
    logs,
    maintenance,
    media,
    modal,
    modal_volume,
    nodes,
    requests,
    routing,
    settings_sync,
    uploads,
    volume,
    workflows,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(cost.router)
api_router.include_router(launch.router)
api_router.include_router(nodes.router)
api_router.include_router(workflows.router)
api_router.include_router(jobs.router)
api_router.include_router(media.router)
api_router.include_router(logs.router)
api_router.include_router(events.router)
api_router.include_router(maintenance.router)
api_router.include_router(requests.router)
api_router.include_router(routing.router)
api_router.include_router(settings_sync.router)
api_router.include_router(modal_volume.router)
api_router.include_router(uploads.router)
api_router.include_router(volume.router)
api_router.include_router(modal.router)

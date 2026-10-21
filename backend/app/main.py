"""ComfyBridge 后端入口。

已接入：
- 管理面 /api/v1（health / nodes / workflows / media / logs / requests / routing / modal_volume / modal）
- 管理面 GUI /bridge（前端构建产物存在时挂载，见 app/gui_static；安装态入口）
- 数据面拦截层（app/proxy）：POST /prompt 拦截（分类/决策/落库/改写 prompt_id），
  /ws 单上游双向中继，GET /object_info 合并云端模型清单，
  其余端点透明流式透传本地 ComfyUI（规划文档 §2.1 反代拦截层）。

启动：仓库开发 uv run uvicorn app.main:app --reload --port 18189；
安装态 comfy-bridge 命令（app/cli.py）。
注意：浏览器指向 :18189 即可透明使用 ComfyUI；生成请求经拦截层路由。
"""

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.websockets import WebSocket

from .api.v1 import api_router
from .core import job_store, maintenance, release_state
from .core.management_auth import install_management_auth
from .gui_static import mount_gui
from .logs import store as logs_store
from .logs.store import close_db, close_stale_pending, init_db
from .models_mgr import cloud_index
from .proxy import local_tracker
from .proxy.object_info import handle_object_info
from .proxy.passthrough import close_upstream_client, forward_request, get_upstream_client
from .proxy.prompt_submit import handle_prompt_submit
from .proxy.ws_relay import relay_local_ws
from .runner import reconcile

# 应用日志（uvicorn 自己的 access/error logger 不传播，不会重复）
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 暂存备份在打开任何 SQLite 连接前应用，避免运行中替换 WAL 数据库。
    try:
        restored = maintenance.apply_pending_restore()
    except maintenance.RestoreBusyError:
        # 另一个实例可能正在替换数据文件；此时继续启动会打开半恢复状态。
        raise
    except Exception as exc:  # noqa: BLE001 - 恢复失败需要降级到当前数据继续启动
        logging.getLogger(__name__).error("启动恢复失败，继续使用当前数据: %s", exc)
    else:
        if restored:
            logging.getLogger(__name__).info("已应用用户数据备份 %s", restored.get("name"))
    # 迁移前备份必须早于 schema 初始化；恢复已先完成，所以备份的是恢复后的数据。
    # 版本未变化时幂等返回，不会在每次启动重复生成备份。
    try:
        release = release_state.prepare_upgrade()
    except Exception as exc:
        try:
            release_state.record_self_check(
                ok=False,
                checks=[
                    {
                        "name": "upgrade_backup",
                        "ok": False,
                        "detail": str(exc),
                    }
                ],
                error=f"迁移前备份失败，未初始化数据库：{exc}",
            )
        except OSError as record_exc:
            logging.getLogger(__name__).error("升级失败且无法写自检记录: %s", record_exc)
        # 数据尚未迁移，必须停在启动阶段；否则后续 schema 修改会绕过备份边界。
        # 自检文件已保留具体错误，用户可在「数据安全」查看并主动重试。
        raise
    init_db()
    job_store.enable_sqlite(str(logs_store.DB_PATH))
    from .core import comfy_scan
    from .modal_volume import jobs as volume_sync_jobs
    from .nodes_mgr import deploy
    from .sync import jobs as sync_jobs
    from .volume_mgr import downloads, moves

    sync_jobs.recover_interrupted()
    volume_sync_jobs.recover_interrupted()
    moves.recover_interrupted()
    downloads.recover_interrupted()
    deploy.recover_interrupted()
    comfy_scan.recover_interrupted()
    stale = close_stale_pending()
    checks = [
        {"name": "database", "ok": True, "detail": "SQLite 初始化完成"},
        {"name": "jobs", "ok": True, "detail": "作业状态恢复完成"},
        {"name": "cloud_runtime", "ok": True, "detail": "后端运行时已就绪"},
    ]
    try:
        release_state.record_self_check(
            ok=True,
            checks=checks,
            version=(release or {}).get("version"),
        )
    except OSError as exc:
        # 自检记录是诊断旁路，不能反过来让已成功初始化的服务起不来。
        logging.getLogger(__name__).warning("启动自检记录写入失败: %s", exc)
    if stale:
        logging.getLogger(__name__).info("已关闭 %d 条遗留的未完成请求记录（执行跟踪不跨重启）", stale)
    get_upstream_client()
    warm = asyncio.create_task(_warm_cloud_index())
    warm_presence = asyncio.create_task(_warm_node_presence())
    warm_backfill = asyncio.create_task(_warm_model_backfill())
    trash_cleanup = asyncio.create_task(_trash_cleanup_loop())
    reconcile.start()
    yield
    warm.cancel()
    warm_presence.cancel()
    warm_backfill.cancel()
    trash_cleanup.cancel()
    await reconcile.stop()
    await local_tracker.aclose()
    await close_upstream_client()
    job_store.close_default()
    close_db()


async def _warm_cloud_index() -> None:
    """延后后台构建云端模型索引，给启动期其他 Modal 只读调用让出限流窗口。"""
    await asyncio.sleep(3.0)
    try:
        await cloud_index.refresh()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("云端模型索引初次构建失败: %s", exc)


async def _warm_node_presence() -> None:
    """启动即后台刷新节点 class 两端在场索引（M3-2；云端侧可能等冷启动，不阻塞就绪）。"""
    from .models_mgr import node_presence

    try:
        await node_presence.refresh()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("节点在场索引初次刷新失败: %s", exc)


async def _warm_model_backfill() -> None:
    """启动即回填历史请求的主模型字段（读图快照提取，成本报表「按模型」维度用）。"""
    from . import cost

    try:
        n = await asyncio.to_thread(cost.backfill_model_names)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("主模型字段回填失败: %s", exc)
    else:
        if n:
            logging.getLogger(__name__).info("已回填 %d 条历史请求的主模型字段", n)


async def _trash_cleanup_loop() -> None:
    """每小时清理一次超过保留期的回收站目录；失败只记日志。"""
    from .volume_mgr import deletion

    while True:
        try:
            await deletion.cleanup_all_expired_trash()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("回收站后台清理失败: %s", exc)
        await asyncio.sleep(3600)


app = FastAPI(title="ComfyBridge", version="0.1.0", lifespan=lifespan)
install_management_auth(app)
app.include_router(api_router)

# 管理面 GUI 挂 /bridge（前端产物存在才挂）：必须在 catch-all 透传之前注册，
# / 与其余路径仍是 ComfyUI 数据面，管理面不侵占数据面根路径
mount_gui(app)


@app.websocket("/ws")
async def ws_proxy(websocket: WebSocket) -> None:
    await relay_local_ws(websocket)


# 核心拦截点：生图/视频请求提交（顺序在 catch-all 之前）。
# ComfyUI 将全部非静态路由同时注册在 /api 前缀下（server.py add api_routes），
# 前端新版本实际提交 /api/prompt，两条路径必须一并接管。
@app.post("/prompt", include_in_schema=False)
@app.post("/api/prompt", include_in_schema=False)
async def prompt_submit(request: Request) -> Response:
    return await handle_prompt_submit(request)


# 中断：本地照常透传；云端有在跑任务时尽力转发 /interrupt（执行器 → execution_interrupted）
@app.post("/interrupt", include_in_schema=False)
@app.post("/api/interrupt", include_in_schema=False)
async def interrupt(request: Request) -> Response:
    from .runner import executor

    executor.interrupt_all()
    return await forward_request(request)


# 根路径不落入 catch-all（path 为空），显式透传 —— ComfyUI 前端挂载于此
@app.get("/", include_in_schema=False)
async def passthrough_root(request: Request) -> Response:
    return await forward_request(request)


# GET /object_info（含 /api 前缀与单节点查询）：合并云端模型清单后再回给前端。
# ComfyUI 的模型下拉与缺失高亮完全由此接口驱动，必须在 catch-all 之前接管。
@app.get("/object_info", include_in_schema=False)
@app.get("/api/object_info", include_in_schema=False)
async def object_info_all(request: Request) -> Response:
    return await handle_object_info(request, None)


@app.get("/object_info/{node_type}", include_in_schema=False)
@app.get("/api/object_info/{node_type}", include_in_schema=False)
async def object_info_node(request: Request, node_type: str) -> Response:
    return await handle_object_info(request, node_type)


# 队列合成：本地 ComfyUI /queue + 在途云端任务（queued/running）。ComfyUI 前端的
# 队列面板（Running / Queue Pending 区块）由该端点驱动；不合成时用户在浏览器上
# 看不到已提交给云端执行器的任务（它们不占用本地队列）。
@app.get("/queue", include_in_schema=False)
@app.get("/api/queue", include_in_schema=False)
async def synthetic_queue(request: Request) -> Response:
    local = await forward_request(request)
    running_local: list = []
    pending_local: list = []
    if local.status_code == 200:
        with contextlib.suppress(Exception):
            data = json.loads(bytes(local.body))
            running_local = list(data.get("queue_running") or [])
            pending_local = list(data.get("queue_pending") or [])
    cloud_rows = [r for r in await logs_store.alist_active() if r.get("final_backend") == "modal"]
    cloud_running = [
        [r["prompt_id"], r["prompt_id"], None, r.get("client_id") or "", None, None, r.get("model_name") or ""]
        for r in cloud_rows
        if r.get("status") == "running"
    ]
    cloud_pending = [
        [r["prompt_id"], r["prompt_id"], None, r.get("client_id") or "", None, None, r.get("model_name") or ""]
        for r in cloud_rows
        if r.get("status") == "queued"
    ]
    return Response(
        content=json.dumps({
            "queue_running": running_local + cloud_running,
            "queue_pending": pending_local + cloud_pending,
        }, ensure_ascii=False).encode(),
        media_type="application/json",
    )


# 其余端点（含静态前端资源、/history、/view、/upload 等）原样透传本地 ComfyUI
@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def passthrough(path: str, request: Request) -> Response:
    return await forward_request(request)

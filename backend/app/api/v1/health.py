"""GET /api/v1/health —— 两端连通性聚合。"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from ...core import comfy_client
from ...core.config import get_settings
from ...modal_volume import client as modal_cli
from ...models_mgr import cloud_index
from ...runner import client as runner_client

logger = logging.getLogger(__name__)

router = APIRouter()


def _cli_status() -> dict:
    return {
        "found": modal_cli.sdk_available(),
        "path": "(in-process SDK)",
        "version": None,
        "logged_in": None,
        "latency_ms": None,
        "app_state": None,
        "error": None,
    }


@router.get("/health/modal-cli")
async def modal_cli_health() -> dict:
    """Modal SDK 启动自检：版本、登录态、网络延迟、应用状态与最近失败。"""
    status = _cli_status()
    if not status["found"]:
        error = modal_cli.ModalCliError(
            "modal Python SDK 不可用（请安装：pip install 'modal>=1.5'）",
            category="not_installed",
        )
        return {**status, "error": error.to_dict(), **modal_cli.diagnostics()}

    started = time.monotonic()
    try:
        status["version"] = f"modal {modal_cli.sdk_version()}"
    except modal_cli.ModalCliError as exc:
        status["error"] = exc.to_dict()
        return {**status, **modal_cli.diagnostics()}

    try:
        app_name = get_settings().modal.app_name
        status["app_state"] = await modal_cli.app_status(app_name)
        status["logged_in"] = True
        status["latency_ms"] = round((time.monotonic() - started) * 1000)
    except modal_cli.ModalCliError as exc:
        logger.exception("app_status 自检失败详情（临时诊断）")
        status["error"] = exc.to_dict()
        if exc.category == "not_logged_in":
            status["logged_in"] = False
    return {**status, **modal_cli.diagnostics()}


@router.get("/health")
async def health() -> dict:
    all_settings = get_settings()
    settings = all_settings.local
    local: dict = {
        "url": settings.comfy_url,
        "online": False,
        "version": None,
        "latency_ms": None,
        "python_version": None,
        "gpu": None,
        "error": None,
    }
    try:
        stats = await comfy_client.fetch_system_stats()
        system = stats.get("system", {})
        devices = stats.get("devices") or []
        local.update(
            online=True,
            version=system.get("comfyui_version"),
            python_version=system.get("python_version"),
            latency_ms=stats["_latency_ms"],
            gpu=devices[0].get("name") if devices else None,
        )
    except Exception as exc:  # noqa: BLE001 —— 不可达是正常状态，转成结构化响应
        local["error"] = str(exc) or type(exc).__name__

    modal_cfg = all_settings.modal
    if modal_cfg.token_id and modal_cfg.token_secret:
        await runner_client.ensure_state()  # 应用部署状态保鲜（TTL 60s）
        cloud = cloud_index.snapshot()
        total = sum(len(v) for v in cloud.values())
        url = runner_client.web_url()
        online = runner_client.available()
        if not modal_cfg.runner_enabled:
            reason = "Runner 通道已停用（modal.runner_enabled=false）"
        elif not url:
            reason = "未配置 web_url（modal deploy modal-runner/runner.py 后写入 config.toml）"
        elif not runner_client.proxy_headers():
            reason = "未配置代理鉴权凭证（modal.proxy_token_id/proxy_token_secret，端点已开代理鉴权）"
        elif online:
            reason = f"Runner 通道就绪；云端模型索引 {total} 项"
        else:
            reason = f"Runner 通道不可用（应用状态 {runner_client.deployed_state() or '未知'}）"
        if cloud_index.last_error():
            reason += f"（索引部分失败: {cloud_index.last_error()}）"
        modal_status = {
            "online": online,
            "reason": reason,
            "cloud_index_files": total,
            "app_state": runner_client.deployed_state(),
            "web_url": url,
        }
    else:
        modal_status = {"online": False, "reason": "未配置 Modal 凭证（设置 → Modal）"}

    return {
        "local": local,
        "modal": modal_status,
        "bridge": {"url": f"http://{all_settings.bridge.listen}", "listen": all_settings.bridge.listen},
    }

"""Modal 通道操作：预热（按需唤醒冷启动的 web_server 容器）。"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter

from ...runner import client as runner_client

router = APIRouter(prefix="/modal", tags=["modal"])

# 容器冷启动（拉镜像外）通常 10~60s；卷大/节点多时更久，给足 300s
_WARMUP_TIMEOUT = 300.0


@router.post("/warmup")
async def warmup() -> dict:
    """GET 云端 /system_stats 直到就绪，返回耗时；容器冷启动期间该请求由 Modal 挂起。"""
    url = runner_client.web_url()
    if not url:
        return {"ok": False, "error": "未配置 modal.web_url（先 modal deploy modal-runner/runner.py 并写入 config.toml）"}
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_WARMUP_TIMEOUT, connect=30)) as client:
            resp = await client.get(f"{url.rstrip('/')}/system_stats", headers=runner_client.proxy_headers())
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "seconds": round(time.monotonic() - start, 1),
            "error": f"云端返回 {exc.response.status_code}",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "seconds": round(time.monotonic() - start, 1),
            "error": str(exc) or type(exc).__name__,
        }
    await runner_client.refresh_state()  # 唤醒后部署状态可能从 stopped 变 deployed
    return {"ok": True, "seconds": round(time.monotonic() - start, 1)}


@router.get("/warmup/status")
async def warmup_status() -> dict:
    """轻量探测：立即返回，不等待冷启动（供前端区分“预热中/已就绪”）。"""
    await runner_client.ensure_state()
    return {"online": runner_client.available(), "app_state": runner_client.deployed_state()}

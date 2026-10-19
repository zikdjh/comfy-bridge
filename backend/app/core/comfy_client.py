"""对本地 ComfyUI 服务端 API 的薄封装。

规划红线：所有对 ComfyUI 协议的假设（端点、响应结构）集中在本文件，
上游版本演进时只改这里。
"""

from __future__ import annotations

import time

import httpx

from .config import get_settings


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=get_settings().local.comfy_url,
        timeout=3.0,
        trust_env=False,
    )


async def fetch_system_stats() -> dict:
    """GET /system_stats —— 版本、延迟、设备信息。

    抛 httpx.HTTPError 表示服务不可达。
    """
    async with _client() as client:
        t0 = time.perf_counter()
        resp = await client.get("/system_stats")
        resp.raise_for_status()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {**resp.json(), "_latency_ms": latency_ms}


async def fetch_object_info() -> dict:
    """GET /object_info —— 全量节点定义（class_type → 输入 schema）。"""
    async with _client() as client:
        resp = await client.get("/object_info", timeout=20.0)
        resp.raise_for_status()
        return resp.json()

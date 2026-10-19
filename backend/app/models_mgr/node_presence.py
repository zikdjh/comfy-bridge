"""节点 class 两端在场索引（M3-2）：本地 / 云端 ComfyUI /object_info 的 class 集合缓存。

用途：路由 node_missing_local / node_missing_cloud 规则——决策路径零 IO，只读快照。
- 本地集合：GET {comfy_url}/object_info（20s 超时；本地未启动视为失败，保留旧集合）
- 云端集合：runner_client.fetch_object_info()（冷启动等待由其 660s 超时兜底）
- 持久化：~/.comfybridge/node_presence.json，后端重启不丢（首次 snapshot() 同步读盘一次）
- 刷新：15 分钟 TTL + kick_refresh_if_stale() 后台单飞；单端失败保留另一端与旧值
- 时间戳用 wall clock（time.time()）：持久化的新鲜度判定必须跨进程成立
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from ..core.config import config_dir, get_settings
from ..runner import client as runner_client

logger = logging.getLogger(__name__)

_TTL = 900.0
_state: dict = {"local": set(), "cloud": set(), "fetched_ms": 0.0, "loaded": False}
_error: str | None = None
_lock = asyncio.Lock()


def _state_path() -> Path:
    return config_dir() / "node_presence.json"


def _load_from_disk() -> None:
    if _state["loaded"]:
        return
    _state["loaded"] = True
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        _state["local"] = set(data.get("local") or [])
        _state["cloud"] = set(data.get("cloud") or [])
        _state["fetched_ms"] = float(data.get("fetched_ms") or 0.0)
    except Exception as exc:  # noqa: BLE001 —— 缺文件/坏文件等同未建索引
        logger.debug("节点在场索引读取持久化文件失败（按未建索引处理）: %s", exc)


def _save_to_disk() -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "fetched_ms": _state["fetched_ms"],
                    "local": sorted(_state["local"]),
                    "cloud": sorted(_state["cloud"]),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("节点在场索引落盘失败: %s", exc)


async def _fetch_local() -> set[str]:
    base = get_settings().local.comfy_url.rstrip("/")
    if "://" not in base:
        raise RuntimeError("本地 ComfyUI 地址未配置")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=5.0),
        trust_env=False,
    ) as cli:
        resp = await cli.get(f"{base}/object_info")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    doc = resp.json()
    return set(doc.keys()) if isinstance(doc, dict) else set()


async def _fetch_cloud() -> set[str]:
    if runner_client.web_url() is None:
        raise RuntimeError("云端 web_url 未配置（跳过云端节点采集）")
    doc = await runner_client.fetch_object_info()
    return set(doc.keys()) if isinstance(doc, dict) else set()


async def refresh(force: bool = False) -> dict[str, set[str]]:
    """重建索引（single-flight）；force=True 跳过 TTL。单端失败保留旧集合。"""
    global _error
    async with _lock:
        _load_from_disk()
        has_data = bool(_state["local"] or _state["cloud"])
        if not force and has_data and time.time() - _state["fetched_ms"] < _TTL:
            return snapshot()

        results = await asyncio.gather(_fetch_local(), _fetch_cloud(), return_exceptions=True)
        errors: list[str] = []
        for side, res in zip(("local", "cloud"), results):
            if isinstance(res, BaseException):
                errors.append(f"{side}: {res}")  # 保留旧集合，不让单端故障清空索引
            elif res:
                _state[side] = res
            else:
                errors.append(f"{side}: 空响应")
        _state["fetched_ms"] = time.time()
        _error = "; ".join(errors)[:300] or None
        if errors:
            logger.warning("节点在场索引部分失败: %s", _error)
        else:
            logger.info("节点在场索引刷新完成：本地 %d / 云端 %d class", len(_state["local"]), len(_state["cloud"]))
        _save_to_disk()
        return snapshot()


def snapshot() -> dict[str, set[str]]:
    """当前缓存（首次调用同步读盘一次，此后纯内存）；两侧都可能为空 = 未就绪。"""
    if not _state["loaded"]:
        _load_from_disk()
    return {"local": set(_state["local"]), "cloud": set(_state["cloud"])}


def stamp() -> tuple[float, int]:
    """索引版本标记：刷新 wall 时间 + class 总数（预览透明度用）。"""
    return (_state["fetched_ms"], len(_state["local"]) + len(_state["cloud"]))


def last_error() -> str | None:
    return _error


def kick_refresh_if_stale() -> None:
    """过期则后台触发一次重建（不阻塞调用方）；供请求路径低成本保鲜。"""
    has_data = bool(_state["local"] or _state["cloud"])
    if has_data and time.time() - _state["fetched_ms"] < _TTL:
        return
    if _lock.locked():
        return

    async def _rebuild() -> None:
        try:
            await refresh(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("节点在场索引后台刷新失败: %s", exc)

    try:
        asyncio.get_running_loop().create_task(_rebuild())
    except RuntimeError:
        pass

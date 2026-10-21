"""整合包更新检查（GitHub Releases Latest）。

- 当前版本读取包根 version.txt（打包脚本写入 git describe，如 v0.1.0-beta.4）；
  文件缺失（源码运行）视为无法检查，不报错。
- 检查请求始终信任系统代理环境变量（trust_env=True）：系统里开了代理（存在
  HTTP_PROXY/HTTPS_PROXY 等）就走，没开就自然直连。与桥接器云端出站的
  use_system_proxy 开关无关——本模块不触碰本地回环，无代理污染风险。
- 24h 节流缓存落 <配置目录>/update_check.json；force=True（设置页手动检查）跳过节流。
- 任何失败静默降级，不抛异常、不阻塞调用方（启动路径友好）。
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from .config import config_dir
from .release_state import is_newer, is_valid_version, read_current_version

logger = logging.getLogger(__name__)

GITHUB_LATEST = "https://api.github.com/repos/zikdjh/comfy-bridge/releases/latest"
_CACHE_PATH = config_dir() / "update_check.json"
_CACHE_TTL = 24 * 3600.0
_HTTP_TIMEOUT = 10.0

def current_version() -> str | None:
    """包内 version.txt 的原始内容（如 v0.1.0-beta.4）；找不到返回 None。"""
    return read_current_version()[0]


def _read_cache() -> dict:
    try:
        if _CACHE_PATH.is_file():
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_cache(data: dict) -> None:
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        logger.debug("update_check 缓存写入失败", exc_info=True)


async def check_update(*, force: bool = False) -> dict:
    """返回 {available, current, latest, release_url, reason, checked_at, cached?}。"""
    now = time.time()
    current = current_version()
    if not current or not is_valid_version(current):
        return {
            "available": False,
            "current": current,
            "latest": None,
            "release_url": "https://github.com/zikdjh/comfy-bridge/releases",
            "reason": "not_packaged",
            "checked_at": None,
        }

    if not force:
        cached = _read_cache()
        if cached.get("checked_at") and (now - cached["checked_at"]) < _CACHE_TTL:
            return {**cached, "cached": True}

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, trust_env=True) as cli:
            resp = await cli.get(GITHUB_LATEST, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub API HTTP {resp.status_code}")
        body = resp.json()
        latest = body.get("tag_name") or ""
        release_url = body.get("html_url") or "https://github.com/zikdjh/comfy-bridge/releases"
        if not latest:
            raise RuntimeError("GitHub 返回无 tag_name")
    except Exception as exc:  # noqa: BLE001 - 网络与解析失败统一静默降级
        logger.info("更新检查失败: %s", exc)
        result = {
            "available": False,
            "current": current,
            "latest": None,
            "release_url": "https://github.com/zikdjh/comfy-bridge/releases",
            "reason": "check_failed",
            "checked_at": now,
        }
        _write_cache(result)
        return result

    result = {
        "available": is_newer(latest, current),
        "current": current,
        "latest": latest,
        "release_url": release_url,
        "reason": "ok",
        "checked_at": now,
    }
    _write_cache(result)
    return result

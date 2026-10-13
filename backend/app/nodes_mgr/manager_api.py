"""ComfyUI-Manager API 兜底采集（M2-1 余项）。

本地节点清单的主数据源是 custom_nodes/ 目录扫描（scan.py）；当本地 ComfyUI 在线
且装有 ComfyUI-Manager 时，向其 /customnode/getlist 兜底采集节点包注册表元数据
（repo URL / 展示名 / 版本），补齐目录扫描拿不到的信息：
- 非 git 目录安装的包（repo=None）：按 cnr_id / 归一化名称匹配出仓库地址
- git 包：补注册表版本号与展示名

纯增强设计：任何失败（ComfyUI 离线、未装 Manager、端点/格式不识别）都静默跳过，
绝不影响主清单。getlist 响应体积大且各 Manager 版本字段有差异，解析按多形态
容错处理（解析契约由单测固定；本机未装 Manager，未在真端点上实测，见 FIXLIST）。
结果模块级缓存 5 分钟 + 单飞，避免清单页轮询反复拉全量注册表。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from ..core.config import get_settings

_TTL_OK = 300.0   # 成功结果缓存 5 分钟（getlist 体积大，避免清单页轮询反复拉全量）
_TTL_FAIL = 30.0  # 失败结果短缓存：刚装上 Manager 后半分钟内即可被识别
_lock = asyncio.Lock()
_cache: tuple[float, dict] | None = None  # (过期 wall_ts, fetch_index() 返回值)


def _normalize(name: str) -> str:
    """cnr 包名（连字符）与本地目录名（下划线/空格）统一到同一匹配键。"""
    return re.sub(r"[-_\s]+", "", (name or "").lower())


def _url_like(value) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://", "git@"))


def parse_getlist(doc) -> dict[str, dict]:
    """从 getlist 响应提取 {归一化匹配键: {repo,title,version}}；不识别的形态返回 {}。

    容错范围：顶层 dict 包 custom_nodes/nodes 列表、顶层直接是列表、单条目缺字段、
    repo 取 reference 或 files[] 中首个 URL 形态值。
    """
    if isinstance(doc, dict):
        entries = doc.get("custom_nodes") or doc.get("nodes") or []
    elif isinstance(doc, list):
        entries = doc
    else:
        return {}
    if not isinstance(entries, list):
        return {}

    out: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        repo_candidates = [entry.get("reference"), *(entry.get("files") or [])]
        repo = next((u for u in repo_candidates if _url_like(u)), None)
        title = next(
            (v for v in (entry.get("title"), entry.get("name_aux"), entry.get("nickname")) if isinstance(v, str)),
            None,
        )
        ver = entry.get("ver") or entry.get("version")
        info = {
            "repo": repo,
            "title": title,
            "version": ver if isinstance(ver, str) and ver else None,
        }
        for key in {entry.get("cnr_id"), title}:
            if isinstance(key, str) and key.strip():
                out.setdefault(_normalize(key), info)
    return out


def apply_enrichment(scan: dict, index: dict) -> int:
    """用 Manager 索引增强目录扫描结果（原地修改），返回匹配上的包数。"""
    matched = 0
    for pkg in scan.get("packages", []):
        info = index.get(_normalize(pkg.get("name", "")))
        if not info:
            continue
        matched += 1
        if not pkg.get("repo") and info.get("repo"):
            pkg["repo"] = info["repo"]
            pkg["repo_via_manager"] = True
        if info.get("version"):
            pkg["manager_version"] = info["version"]
        if info.get("title") and info["title"] != pkg.get("name"):
            pkg["manager_title"] = info["title"]
    return matched


async def _get_json(base: str, path: str) -> dict | list:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as cli:
        resp = await cli.get(f"{base.rstrip('/')}{path}")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.json()


async def fetch_index() -> dict:
    """抓取 Manager 注册表索引（成功缓存 5 分钟 / 失败 30 秒，单飞）。

    返回 {"online", "index", "entries", "error"}；任何失败都降级为 online=False。
    """
    global _cache
    if _cache and time.time() < _cache[0]:
        return _cache[1]
    async with _lock:
        if _cache and time.time() < _cache[0]:
            return _cache[1]
        base = get_settings().local.comfy_url
        result: dict[str, Any] = {"online": False, "index": None, "entries": 0, "error": None}
        if "://" not in base:
            result["error"] = "本地 ComfyUI 地址未配置"
        else:
            try:
                doc = await _get_json(base, "/customnode/getlist")
                index = parse_getlist(doc)
                if not index:
                    raise RuntimeError("响应格式不识别")
                result.update({"online": True, "index": index, "entries": len(index)})
            except Exception as exc:  # noqa: BLE001 —— 兜底采集失败静默降级
                result["error"] = str(exc) or type(exc).__name__
        _cache = (time.time() + (_TTL_OK if result["online"] else _TTL_FAIL), result)
        return result

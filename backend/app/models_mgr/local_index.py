"""本地模型清单：桥接器侧对「本地有哪些模型」的近似视图（云端专有模型判定用）。

扫描范围与本地 ComfyUI 的实际搜索路径对齐：
- model_dirs_resolved()（配置映射，D 共享目录优先）
- comfy_root/models/ 下全部子目录（仓库内置目录）
任一命中即视为本地存在；60s 缓存，本地扫描失败时返回空（退化为不做判定）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from ..core.config import get_settings, model_dirs_resolved
from ..core.constants import MODEL_FILE_EXTS

logger = logging.getLogger(__name__)

_TTL = 60.0
_cache: dict[str, set[str]] = {}
_cached_at = 0.0


def _iter_model_files(root: Path, max_depth: int = 3):
    if not root.is_dir():
        return
    base_depth = len(root.parts)
    for path in root.rglob("*"):
        if len(path.parts) - base_depth > max_depth:
            continue
        if path.is_file() and path.suffix.lower() in MODEL_FILE_EXTS:
            yield path


def _scan() -> dict[str, set[str]]:
    roots: dict[str, Path] = {cat: Path(p) for cat, p in model_dirs_resolved().items()}
    builtin = Path(get_settings().local.comfy_root) / "models"
    if builtin.is_dir():
        for child in sorted(builtin.iterdir()):
            if child.is_dir() and child.name not in roots:
                roots[child.name] = child

    index: dict[str, set[str]] = {}
    for cat, path in roots.items():
        names = index.setdefault(cat, set())
        for file in _iter_model_files(path):
            # ComfyUI 组合框值是分类目录内相对路径；存相对路径 + 裸文件名两种形态
            names.add(file.name)
            names.add(file.relative_to(path).as_posix())
    return index


async def local_names() -> dict[str, set[str]]:
    """分类 → 模型名集合（含相对路径与裸文件名）；带 TTL 缓存。

    扫描放线程池：模型盘大时 rglob 可能耗时数秒，而调用点在 /prompt 路由热路径上，
    同步执行会阻塞事件循环（殃及 WS 中继与 SSE）。
    """
    global _cache, _cached_at
    now = time.monotonic()
    if _cache and now - _cached_at < _TTL:
        return _cache
    try:
        _cache = await asyncio.to_thread(_scan)
    except Exception as exc:  # noqa: BLE001 —— 扫描失败不阻断路由，退化为空视图
        logger.warning("本地模型扫描失败: %s", exc)
        _cache = {}
    _cached_at = now
    return _cache


def invalidate() -> None:
    global _cache, _cached_at
    _cache, _cached_at = {}, 0.0

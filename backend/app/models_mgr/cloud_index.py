"""云端模型索引：Modal 数据卷 models/ 的缓存视图。

- 判定「云端专有模型」（本地无副本、云端有）与 /object_info 合并的数据源
- 后台启动构建 + 300s TTL 懒刷新 + 同步作业完成后强制重建
- 凭证缺失 / CLI 不可用 / 列举失败 → 空索引（云端判定退化为不生效，行为同透传本地）
- CLI 列举是子进程调用，按分类并发执行；单分类失败不影响其余分类
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time

from ..core.config import modal_configured, model_dirs_resolved
from ..modal_volume import client
from ..modal_volume.client import ModalCliError

logger = logging.getLogger(__name__)

_TTL = 300.0
_index: dict[str, set[str]] = {}
_fetched_at = 0.0
_error: str | None = None
_lock = asyncio.Lock()


async def refresh(force: bool = False) -> dict[str, set[str]]:
    """重建索引（single-flight）；force=True 跳过 TTL。返回当前索引。"""
    global _index, _fetched_at, _error
    async with _lock:
        if not force and _index and time.monotonic() - _fetched_at < _TTL:
            return _index
        if not modal_configured():
            _index, _error = {}, None
            _fetched_at = time.monotonic()
            return _index

        cats = sorted(model_dirs_resolved())
        sem = asyncio.Semaphore(4)  # CLI 子进程并发上限，避免 8 路齐发时瞬时失败

        async def _list(cat: str) -> list[dict]:
            async with sem:
                last: BaseException | None = None
                for attempt in range(2):  # 瞬时失败重试一次
                    try:
                        return await client.list_files(f"models/{cat}")
                    except ModalCliError as exc:
                        last = exc
                        await asyncio.sleep(0.5 * (attempt + 1))
                raise last  # type: ignore[misc]

        results = await asyncio.gather(*(_list(cat) for cat in cats), return_exceptions=True)
        index: dict[str, set[str]] = {}
        errors: list[str] = []
        for cat, res in zip(cats, results):
            if isinstance(res, BaseException):
                if not isinstance(res, ModalCliError):
                    errors.append(f"{cat}: {res}")
                continue
            names: set[str] = set()
            for entry in res:
                names.add(entry["name"])
                names.add(entry["name"].rsplit("/", 1)[-1])
            index[cat] = names
        if errors:
            logger.warning("云端模型索引部分失败: %s", "; ".join(errors)[:300])
        total = sum(len(v) for v in index.values())
        logger.info("云端模型索引刷新完成：%d 分类 / %d 项", len(index), total)
        _index, _error = index, "; ".join(errors)[:300] or None
        _fetched_at = time.monotonic()
        return _index


def snapshot() -> dict[str, set[str]]:
    """当前缓存索引（不做 IO）；可能为空（未构建 / 未配置凭证）。"""
    return _index


def stamp() -> tuple[float, int]:
    """索引版本标记：刷新时间 + 总条目数（object_info 合并缓存失效用）。"""
    return (_fetched_at, sum(len(v) for v in _index.values()))


def last_error() -> str | None:
    return _error


def kick_refresh_if_stale() -> None:
    """过期则后台触发一次重建（不阻塞调用方）；供请求路径低成本保鲜。"""
    if _index and time.monotonic() - _fetched_at < _TTL:
        return
    if _lock.locked():
        return

    async def _rebuild() -> None:
        try:
            await refresh(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("云端模型索引后台刷新失败: %s", exc)

    try:
        asyncio.get_running_loop().create_task(_rebuild())
    except RuntimeError:
        pass

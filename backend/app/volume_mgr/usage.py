"""数据卷用量统计引擎：遍历 token 下所有卷递归累加文件大小（内存缓存 + 后台计算）。

- 触发：GET /volume/volumes 时缓存过期（TTL 60s）则在后台启动一轮计算，立即返回
  旧快照 + computing=true，前端轮询直到刷新完成；
- 遍历：逐卷 BFS 展开，list_dir 并发限流（信号量 6 个 CLI 子进程），目录条目大小
  不可靠（非聚合值），只累加文件条目的人类可读 size；
- 阈值：总用量 ≥512GiB 标黄（接近额度），≥1024GiB 标红（超出免费额度）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..modal_volume import client as modal_cli
from ..modal_volume.client import ModalCliError

_TTL = 60.0
_CONCURRENCY = 6
_WARN_BYTES = 512 * 1024**3
_OVER_BYTES = 1024 * 1024**3

_LOCK = asyncio.Lock()
_CACHE: dict = {
    "generation": 0,        # 账号切换失效计数：代际不匹配的计算结果直接丢弃
    "computing": False,
    "computed_mono": -1e9,  # 上次完成的单调时钟
    "computed_ms": 0,       # 上次完成的墙钟（给前端展示）
    "volumes": [],          # [{name, created_at, total_bytes, file_count, error}]
    "total_bytes": 0,
    "file_count": 0,
    "error": None,
}


def _status(total_bytes: int) -> str:
    if total_bytes >= _OVER_BYTES:
        return "over"      # 红：超出免费额度
    if total_bytes >= _WARN_BYTES:
        return "warn"      # 黄：接近额度
    return "ok"


def get_usage(force: bool = False) -> dict:
    """返回缓存快照；过期/强制时在后台启动新一轮计算（单飞，不叠任务）。"""
    expired = time.monotonic() - _CACHE["computed_mono"] > _TTL
    if not _CACHE["computing"] and (expired or force):
        _CACHE["computing"] = True  # 先占位再投递，事件循环内无并发窗口
        try:
            asyncio.get_running_loop().create_task(_compute())
        except RuntimeError:  # 无事件循环（不应发生，路由内必有）
            _CACHE["computing"] = False
    return _snapshot()


def invalidate_cache() -> None:
    """账号切换后清空卷用量快照（volume 已随账号变化，docs/多账号维护设计.md §3.3）。

    generation 自增让在途的旧账号计算在写回前被判定过期丢弃。
    """
    _CACHE["generation"] = int(_CACHE.get("generation", 0)) + 1
    _CACHE.update(
        computing=False,
        computed_mono=-1e9,
        computed_ms=0,
        volumes=[],
        total_bytes=0,
        file_count=0,
        error=None,
    )

def _snapshot() -> dict:
    return {
        "computing": _CACHE["computing"],
        "computed_ms": _CACHE["computed_ms"],
        "total_bytes": _CACHE["total_bytes"],
        "file_count": _CACHE["file_count"],
        "status": _status(_CACHE["total_bytes"]),
        "volumes": _CACHE["volumes"],
        "error": _CACHE["error"],
    }


async def _compute() -> None:
    async with _LOCK:
        gen = _CACHE["generation"]
        try:
            out = await modal_cli.run("volume", "list", "--json")
            rows = modal_cli.cli_json_list(out)
            names = [str(r.get("name")) for r in rows if r.get("name")]
            results = await asyncio.gather(*(_volume_usage(n, r) for n, r in zip(names, rows)))
            volumes = sorted(results, key=lambda v: v["total_bytes"], reverse=True)
            if _CACHE["generation"] != gen:  # 计算期间发生账号切换，结果作废
                return
            _CACHE.update(
                volumes=volumes,
                total_bytes=sum(v["total_bytes"] for v in volumes),
                file_count=sum(v["file_count"] for v in volumes),
                error=None,
                computed_mono=time.monotonic(),
                computed_ms=int(time.time() * 1000),
            )
        except Exception as exc:  # noqa: BLE001 —— CLI 缺失/凭证问题都落到 error
            _CACHE["error"] = str(exc)[:300]
            _CACHE["computed_mono"] = time.monotonic()
            _CACHE["computing"] = False
            return
        _CACHE["computing"] = False


async def _volume_usage(name: str, row: dict) -> dict:
    entry: dict[str, Any] = {
        "name": name,
        "created_at": str(row.get("created_at") or ""),
        "total_bytes": 0,
        "file_count": 0,
        "error": None,
    }
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def walk(rel_dir: str) -> None:
        async with sem:
            try:
                entries = await modal_cli.list_dir(rel_dir, volume=name)
            except ModalCliError as exc:
                entry["error"] = str(exc)[:200]
                return
        subs: list[str] = []
        for e in entries:
            if e["type"] in ("dir", "directory"):
                subs.append(f"{rel_dir}/{e['name']}" if rel_dir else e["name"])
            else:
                entry["total_bytes"] += modal_cli.parse_size(e["size"])
                entry["file_count"] += 1
        if subs:
            await asyncio.gather(*(walk(s) for s in subs))

    try:
        await walk("")
    except Exception as exc:  # noqa: BLE001
        entry["error"] = entry["error"] or str(exc)[:200]
    return entry


async def count_files(volume: str, rel_dir: str) -> int:
    """递归统计卷内某目录的文件数（删除守卫用）。目录不存在时抛 ModalCliError。"""
    sem = asyncio.Semaphore(_CONCURRENCY)
    count = 0

    async def walk(rel: str) -> None:
        nonlocal count
        async with sem:
            entries = await modal_cli.list_dir(rel, volume=volume)
        subs: list[str] = []
        for e in entries:
            if e["name"].startswith("."):
                continue  # mkdir 的 .keep 标记等隐藏文件不算内容：只剩隐藏文件的目录仍可删
            if e["type"] in ("dir", "directory"):
                subs.append(f"{rel}/{e['name']}" if rel else e["name"])
            else:
                count += 1
        if subs:
            await asyncio.gather(*(walk(s) for s in subs))

    await walk(rel_dir)
    return count

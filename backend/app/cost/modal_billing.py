"""Modal 账单实时查询（M4-1 补充：账户级真实消耗，与本地按次估算互补）。

走 SDK 网关 `Workspace.billing.summary/report`，与卷操作复用同一 client 与凭证缓存。

口径：单月窗口，对齐 UTC 月界（cycle=YYYY-MM）；metered_cost = Modal 计量
（含 Deployed/Ephemeral Apps、Volumes 等，不止 GPU 跑图），billed_cost =
积分抵扣后实际计费（> 0 即赠金/额度已耗尽），credits = 本期积分抵扣。
无公开「剩余积分总额」接口（plan 总额仅仪表盘会话可见）。

缓存：成功 120s、失败 20s（前端 30s 轮询）；任何异常转 ok=false + error 展示。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from ..modal_volume import client as modal_cli

_TTL_OK = 120.0
_TTL_FAIL = 20.0


def current_cycle() -> str:
    """当前 UTC 计费月（YYYY-MM），与 CLI --for / SDK cycle 语义对齐。"""
    return datetime.now(UTC).strftime("%Y-%m")


_cache: dict = {"at": -1e9, "data": None}


def _cache_fresh(now: float) -> bool:
    ttl = _TTL_OK if (_cache["data"] or {}).get("ok") else _TTL_FAIL
    return now - _cache["at"] < ttl


async def fetch(force: bool = False) -> dict:
    """本月 Modal 账单（SDK，带缓存；永不抛出，失败转 ok=false + error）。"""
    now = time.monotonic()
    if not force and _cache["data"] is not None and _cache_fresh(now):
        return _cache["data"]
    cycle = current_cycle()
    try:
        data = await modal_cli.billing_summary(cycle)
    except Exception as exc:  # noqa: BLE001 —— 账单失败不阻塞本地成本汇总
        data = {"ok": False, "error": str(exc)[:300]}
    if data.get("ok"):
        data = {
            **data,
            "cycle": cycle,
            "period_start": cycle + "-01",
        }
    _cache.update(at=now, data=data)
    return data


# —— 账单明细（report）：app × 日（× 资源）粒度真实计费，逐项对账的数据源（规划 §8.3 v2） ——

_REPORT_TTL_OK = 600.0  # 天粒度历史数据，缓存可比 summary 长
_REPORT_TTL_FAIL = 60.0
_report_cache: dict[tuple[str, str], tuple[float, dict]] = {}


async def report(start_date: str, end_date: str, force: bool = False) -> dict:
    """区间账单明细（[start, end)，天粒度 × 资源拆分；永不抛出，失败转 ok=false）。"""
    key = (start_date, end_date)
    now = time.monotonic()
    cached = _report_cache.get(key)
    if cached and not force:
        at, data = cached
        if now - at < (_REPORT_TTL_OK if data.get("ok") else _REPORT_TTL_FAIL):
            return data
    try:
        start = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
        end = datetime.fromisoformat(end_date).replace(tzinfo=UTC)
        data = await modal_cli.billing_report(start, end)
    except Exception as exc:  # noqa: BLE001 —— 账单失败不阻塞本地成本汇总
        data = {"ok": False, "error": str(exc)[:300]}
    _report_cache[key] = (now, data)
    return data

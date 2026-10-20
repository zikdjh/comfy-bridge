"""成本估算与预算护栏（规划文档 §3.6 · M4-1）。

估算模型：GPU 单价（[bridge] gpu_hourly_rate_usd，设置页可改）× 请求墙钟秒数
（执行器从云端提交（含冷启动等待）计时到终态）。成本按次写入 requests 表
（gpu_seconds / est_cost_usd），按日/月聚合在读取时 SQL 现算，不设夜间重算任务。

估算口径（有意从简）：
- @modal.concurrent 复用容器时多个请求墙钟重叠，按请求直加 = 保守上限；
- scaledown_window 保活窗口无请求归属，不计入；
- 逐项对账属 v2（规划文档 §8.3）；账户级本期真实消耗已可实时查看（modal_billing 子进程查询）。

预算护栏：monthly_budget_usd > 0 启用；本月已用 ≥ 上限视为超限，按
on_budget_exceed 回落本地（并告警）或拒绝执行。决策留痕 rule_trace，
仪表盘横幅读 summary.budget.exceeded。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from ..core.config import get_settings
from ..core.constants import gpu_type
from ..logs import store
from ..routing.engine import extract_primary_model
from . import modal_billing


def est_cost_usd(gpu_seconds: float, rate: float | None = None) -> float:
    """墙钟秒数 → 估算美元（默认读配置单价；负值按 0 计）。"""
    r = get_settings().bridge.gpu_hourly_rate_usd if rate is None else rate
    return round(max(gpu_seconds, 0.0) / 3600.0 * r, 5)


async def arecord_cost(prompt_id: str, gpu_seconds: float) -> float:
    """按次入账，返回估算金额（执行器终态路径调用）。"""
    cost = est_cost_usd(gpu_seconds)
    await store.arecord_cost(prompt_id, gpu_seconds, cost)
    return cost


def _local_day_start_ms(dt: datetime) -> int:
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def totals(start_ms: int | None = None, end_ms: int | None = None) -> dict:
    """区间成本聚合（DB 查询放调用方线程池，见 a* 封装）。"""
    return store.cost_totals(start_ms, end_ms)


def month_usage() -> dict:
    """本月已用（本地时区月初零点起）。"""
    return store.cost_totals(_local_day_start_ms(datetime.now().astimezone().replace(day=1)))


async def amonth_usage() -> dict:
    return await asyncio.to_thread(month_usage)


def budget_state() -> dict:
    """预算护栏现状（month_usage 一次 SQL + 配置读取；prompt_submit 决策前调用）。"""
    b = get_settings().bridge
    used = month_usage()["est_cost_usd"]
    limit = b.monthly_budget_usd
    exceeded = bool(limit > 0 and used >= limit)
    return {
        "enabled": limit > 0,
        "monthly_usd": limit,
        "used_usd": used,
        "remaining_usd": round(max(limit - used, 0.0), 5) if limit > 0 else None,
        "exceeded": exceeded,
        "on_exceed": b.on_budget_exceed,
    }


async def abudget_state() -> dict:
    return await asyncio.to_thread(budget_state)


def summary() -> dict:
    """控制台「额度与成本」汇总：今日/本月 + 近 30 天逐日 + 预算现状。"""
    today = store.cost_totals(_local_day_start_ms(datetime.now().astimezone()))
    return {
        "gpu_type": gpu_type(),
        "gpu_hourly_rate_usd": get_settings().bridge.gpu_hourly_rate_usd,
        "today": today,
        "month": month_usage(),
        "daily": store.cost_daily(30),
        "budget": budget_state(),
        "note": "估算 = GPU 单价 × 请求墙钟秒数（含冷启动等待）；不含 scaledown 保活窗口；"
                "并发复用容器时按请求直加（保守上限）。",
    }


async def afull_summary() -> dict:
    """summary() + Modal 账单实时数据（子进程查询带缓存；失败仅转 modal.error）。"""
    data = await asyncio.to_thread(summary)
    data["modal"] = await modal_billing.fetch()
    return data


def backfill_model_names() -> int:
    """历史请求主模型回填（报表「按模型」维度）：从图快照提取一次。

    快照缺失/损坏/无可识别模型时写空串哨兵（聚合按「未记录」展示），
    避免每次启动重复扫描同一批记录。
    """
    updated = 0
    for row in store.requests_missing_model():
        model = ""
        try:
            payload = json.loads((store.GRAPH_DIR / row["graph_file"]).read_bytes())
            model = extract_primary_model(payload.get("prompt") or {}) or ""
        except (OSError, ValueError):
            pass  # 快照已被淘汰或损坏：留空哨兵，不再重扫
        store.set_model_name(row["id"], model)
        if model:
            updated += 1
    return updated

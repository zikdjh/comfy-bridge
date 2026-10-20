"""逐项账单对账（规划 §8.3 v2）：本地按次估算 vs Modal 账单明细。

- 本地侧：requests 表按 UTC 日聚合，只含已入账的云端请求（估算口径见 cost 模块）；
- Modal 侧：SDK `Workspace.billing.report`（app × 日 × 资源真实计量，
  网关内拉取并带缓存，见 modal_billing.report）；其中 GPU（GPU_TYPE 型号）
  资源行与本地估算同口径，CPU/内存/卷等其余资源只在「全资源计量」里呈现；
- 对比指标：本地估算 vs Modal GPU 计量 vs Modal 全资源计量，比值 = Modal GPU / 本地
  估算——持续偏离 1 说明单价配置（gpu_hourly_rate_usd）或估算口径需要校准；
- 日界为 UTC（Modal 账单口径）：与控制台「近 30 天逐日」（本地日界）最多差数小时，
  区间总量不受影响；仅支持完整 UTC 月（与账单 cycle 语义一致）。
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from ..core.constants import gpu_type
from ..logs import store
from . import modal_billing

_CYCLE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

def _note() -> str:
    return (
        "对账口径：本地估算 = GPU 单价 × 请求墙钟秒数（含冷启动等待）；Modal GPU = 账单明细中 "
        f"{gpu_type()} 资源的真实计量；日界为 UTC（Modal 账单口径），"
        "与控制台逐日图（本地日界）最多差数小时。"
    )

# 比例偏差告警阈值：Modal GPU 计量 / 本地估算 落在 [1/T, T] 之外即提醒校准单价。
# 短期偏离属正常（冷启动等待、scaledown 保活窗口、并发复用计入方式不同），
# 这里只做提示，不改配置、不拦截请求。
RATIO_WARN_FACTOR = 1.5
DIVERGENCE_MESSAGE = (
    "Modal GPU 计量与本地估算的比值持续偏离 1，请核对设置中的 GPU 单价"
    "（gpu_hourly_rate_usd）与 Modal 实际价格；本地护栏只是估算，"
    "不代表 Modal 账户消费上限。"
)


def validate_cycle(cycle: str) -> str:
    """cycle 必须是合法计费月 YYYY-MM，否则 422。"""
    if not _CYCLE_RE.fullmatch(cycle or ""):
        raise ValueError("cycle 必须是 YYYY-MM 格式的计费月（如 2026-10）")
    return cycle


def utc_bounds(cycle: str) -> tuple[int, int, str, str]:
    """计费月 → (start_ms, end_ms, start_date, end_date)（UTC 月界，end 为-exclusive 下月初）。"""
    year, month = int(cycle[:4]), int(cycle[5:7])
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    return (
        int(start.timestamp() * 1000),
        int(end.timestamp() * 1000),
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )


async def areconcile(cycle: str) -> dict:
    """单月对账：本地 SQL + Modal SDK 明细（缓存）；Modal 侧失败不阻塞本地侧展示。"""
    start_ms, end_ms, start_date, end_date = utc_bounds(cycle)
    local_totals, local_daily, modal_rep = await asyncio.gather(
        store.acost_totals(start_ms, end_ms),
        store.acost_daily_utc(start_ms, end_ms),
        modal_billing.report(start_date, end_date),
    )
    by_date = {r["date"]: r for r in local_daily}
    modal_daily = modal_rep.get("daily") or {} if modal_rep.get("ok") else {}
    dates = sorted(set(by_date) | set(modal_daily))
    daily = [
        {
            "date": d,
            "local_requests": by_date[d]["requests"] if d in by_date else 0,
            "local_usd": by_date[d]["est_cost_usd"] if d in by_date else 0.0,
            "modal_gpu_usd": modal_daily.get(d, {}).get("gpu_usd", 0.0),
            "modal_total_usd": modal_daily.get(d, {}).get("total_usd", 0.0),
        }
        for d in dates
    ]
    local_est = local_totals["est_cost_usd"]
    modal_gpu = modal_rep.get("gpu_usd") if modal_rep.get("ok") else None
    ratio = round(modal_gpu / local_est, 3) if modal_gpu and local_est > 0 else None
    diverged = ratio is not None and not (
        1 / RATIO_WARN_FACTOR <= ratio <= RATIO_WARN_FACTOR
    )
    return {
        "cycle": cycle,
        "local": local_totals,
        "modal": modal_rep,
        "daily": daily,
        "ratio_gpu_vs_est": ratio,
        "ratio_warn_factor": RATIO_WARN_FACTOR,
        "diverged": diverged,
        "divergence_message": DIVERGENCE_MESSAGE if diverged else None,
        "note": _note(),
    }

"""成本汇总与预算设置（规划文档 §5 GET /api/cost/summary · PUT /api/cost/budget）。

仪表盘「额度与成本」卡片与设置页「护栏与预算」表单的数据源：
- summary 聚合 requests 表的成本记录（今日/本月/近 30 天逐日 + 预算现状）
- PUT /budget 增量更新 [bridge] 的月度预算/超限动作/GPU 单价并即时热生效，
  返回更新后的完整 summary（卡片单次往返刷新）

报表 v2（规划 §8.3）：
- GET /report 成本按工作流/模型聚合（近 N 天，金额降序）
- GET /reconciliation 逐项账单对账（本地按次估算 vs Modal 账单明细，UTC 月）
- GET /export CSV 导出（明细流水 / 聚合），utf-8-sig 兼容 Excel 直接打开
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ... import cost as cost_svc
from ...cost import reconciliation
from .config import update_bridge_section

router = APIRouter(prefix="/cost", tags=["cost"])


@router.get("/summary")
async def cost_summary() -> dict:
    return await cost_svc.afull_summary()


class BudgetBody(BaseModel):
    # None = 不修改该项；monthly_budget_usd = 0 表示关闭护栏
    monthly_budget_usd: float | None = None
    on_budget_exceed: Literal["fallback_local", "reject"] | None = None
    gpu_hourly_rate_usd: float | None = None


@router.put("/budget")
async def put_budget(body: BudgetBody) -> dict:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates.get("monthly_budget_usd", 0) < 0:
        raise HTTPException(status_code=422, detail="月度预算不能为负（0 = 关闭护栏）")
    if "gpu_hourly_rate_usd" in updates and updates["gpu_hourly_rate_usd"] <= 0:
        raise HTTPException(status_code=422, detail="GPU 单价必须大于 0")
    await update_bridge_section(updates)
    return await cost_svc.afull_summary()


# —— 报表 v2（规划 §8.3）：聚合 / 对账 / 导出 ——

_BREAKDOWN_SCOPES = ("workflow", "model")
_DAYS_RANGE = (1, 365)


def _check_days(days: int) -> int:
    if not _DAYS_RANGE[0] <= days <= _DAYS_RANGE[1]:
        raise HTTPException(status_code=422, detail=f"days 需在 {_DAYS_RANGE[0]}~{_DAYS_RANGE[1]} 之间")
    return days


def _attach_share(rows: list[dict]) -> list[dict]:
    """聚合行就地补 share（占比）；total 为 0 时全部记 0。/report 与 /export 共用。"""
    total = sum(r["est_cost_usd"] for r in rows)
    for r in rows:
        r["share"] = round(r["est_cost_usd"] / total, 4) if total > 0 else 0.0
    return rows


@router.get("/report")
async def cost_report(
    by: Literal["workflow", "model"] = "workflow",
    days: int = 30,
    limit: int = 20,
) -> dict:
    """成本按维度聚合（报表 v2）：返回行 + 合计，供表格与占比展示。"""
    _check_days(days)
    rows = _attach_share(await cost_svc.store.acost_breakdown(by, days, min(max(limit, 1), 200)))
    total = sum(r["est_cost_usd"] for r in rows)
    return {"by": by, "days": days, "rows": rows, "total_est_cost_usd": round(total, 5)}


@router.get("/reconciliation")
async def cost_reconciliation(cycle: str | None = None) -> dict:
    """逐项账单对账（默认当前 UTC 计费月）：本地按次估算 vs Modal 账单明细。"""
    cycle = cycle or reconciliation.modal_billing.current_cycle()
    try:
        reconciliation.validate_cycle(cycle)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return await reconciliation.areconcile(cycle)


def _csv_response(name: str, header: list[str], rows: list[list]) -> Response:
    """CSV 组包（utf-8-sig 让 Excel 正确识别中文；错误文本内含逗号/换行由 csv 模块转义）。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"),  # UTF-8 BOM
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.csv"'},
    )


@router.get("/export")
async def cost_export(
    scope: Literal["entries", "workflow", "model"] = "entries",
    days: int = 30,
) -> Response:
    """CSV 导出：entries = 逐条成本流水，workflow/model = 同 /report 的聚合行。"""
    _check_days(days)
    if scope == "entries":
        entries = await cost_svc.store.acost_entries(days)
        rows = [
            [
                e["created_at"], e.get("workflow_name") or "", e.get("model_name") or "",
                e["gen_type"], e["final_backend"], e["status"],
                e["gpu_seconds"], e["est_cost_usd"], (e.get("error") or "").replace("\n", " ")[:300],
            ]
            for e in entries
        ]
        return _csv_response(
            f"comfybridge-cost-entries-{days}d",
            ["时间", "工作流", "主模型", "类型", "通道", "状态", "GPU秒", "估算USD", "错误"],
            rows,
        )
    rows = [
        [r["key"], r["requests"], r["gpu_seconds"], r["est_cost_usd"], r["share"]]
        for r in _attach_share(await cost_svc.store.acost_breakdown(scope, days, 200))
    ]
    label = "工作流" if scope == "workflow" else "主模型"
    return _csv_response(
        f"comfybridge-cost-{scope}-{days}d",
        [label, "请求数", "GPU秒", "估算USD", "占比"],
        rows,
    )

"""成本汇总与预算设置（规划文档 §5 GET /api/cost/summary · PUT /api/cost/budget）。

仪表盘「额度与成本」卡片与设置页「护栏与预算」表单的数据源：
- summary 聚合 requests 表的成本记录（今日/本月/近 30 天逐日 + 预算现状）
- PUT /budget 增量更新 [bridge] 的月度预算/超限动作/GPU 单价并即时热生效，
  返回更新后的完整 summary（卡片单次往返刷新）
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import cost as cost_svc
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
    await asyncio.to_thread(update_bridge_section, updates)
    return await cost_svc.afull_summary()

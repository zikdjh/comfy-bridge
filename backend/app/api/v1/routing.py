"""路由决策 API：干跑预览（规划文档 §3.1 POST /api/routing/preview）。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ... import cost as cost_svc
from ...routing.engine import classify_graph, decide

router = APIRouter(prefix="/routing", tags=["routing"])


class RoutingPreviewBody(BaseModel):
    # 二选一：传 {"prompt": <API 格式图>} 或直接把图 dict 作为顶层（键为节点 id）
    prompt: dict | None = None
    workflow_name: str | None = None
    manual_backend: str | None = None


@router.post("/preview")
async def routing_preview(body: dict) -> dict:
    """不执行，只返回分类事实 + 逐规则匹配过程 + 最终决策（UI 干跑可视化用）。

    预算护栏现状随决策一并求值：超限时 trace 会出现 budget_guardrail 步骤，
    与真实提交路径一致。
    """
    prompt = body.get("prompt") if isinstance(body.get("prompt"), dict) else body
    workflow_name = body.get("workflow_name")
    manual_backend = body.get("manual_backend") or body.get("x-bridge-backend")
    facts = classify_graph(prompt)
    budget = await cost_svc.abudget_state()
    decision = decide(facts, workflow_name=workflow_name, manual_backend=manual_backend, budget=budget)
    return {
        "facts": facts,
        "backend": decision.backend,
        "final_backend": decision.final_backend,
        "fallback": decision.fallback,
        "budget": budget,
        "trace": decision.trace,
    }

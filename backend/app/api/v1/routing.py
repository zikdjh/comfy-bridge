"""路由决策 API：干跑预览 + 自定义规则链 CRUD（规划文档 §3.1 / M3）。

规则链语义见 routing/engine.py 模块注释：manual/tag/cloud_model/default 固定，
bridge.rules 自定义链非空时替换内置 gen_type/size 两步。CRUD 用集合级
GET/PUT（整链替换），行级增删改由设置页在内存完成后一次提交。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import cost as cost_svc
from ...core.config import get_settings
from ...logs import store
from ...models_mgr import node_presence
from ...routing.engine import RULE_KINDS, classify_graph, decide, validate_rules
from .config import update_bridge_section

router = APIRouter(prefix="/routing", tags=["routing"])


class RoutingPreviewBody(BaseModel):
    # 二选一：传 {"prompt": <API 格式图>} 或直接把图 dict 作为顶层（键为节点 id）
    prompt: dict | None = None
    workflow_name: str | None = None
    manual_backend: str | None = None


class RoutingRulesBody(BaseModel):
    rules: list[dict]


@router.post("/preview")
async def routing_preview(body: dict) -> dict:
    """不执行，只返回分类事实 + 逐规则匹配过程 + 最终决策（UI 干跑可视化用）。

    预算护栏现状随决策一并求值：超限时 trace 会出现 budget_guardrail 步骤，
    与真实提交路径一致。
    """
    raw_prompt = body.get("prompt")
    prompt = raw_prompt if isinstance(raw_prompt, dict) else body
    workflow_name = body.get("workflow_name")
    manual_backend = body.get("manual_backend") or body.get("x-bridge-backend")
    facts = classify_graph(prompt)
    node_presence.kick_refresh_if_stale()  # node_missing_* 规则的数据源保鲜（后台，不阻塞干跑）
    presence = node_presence.snapshot()
    budget = await cost_svc.abudget_state()
    cloud_active = await store.acount_active_modal()  # 并发护栏现状（干跑如实呈现）
    decision = decide(
        facts,
        workflow_name=workflow_name,
        manual_backend=manual_backend,
        budget=budget,
        presence=presence,
        cloud_active=cloud_active,
    )
    p_total = len(presence["local"]) + len(presence["cloud"])
    return {
        "facts": facts,
        "backend": decision.backend,
        "final_backend": decision.final_backend,
        "fallback": decision.fallback,
        "budget": budget,
        "cloud_active": cloud_active,
        "presence": {"ready": p_total > 0, "local": len(presence["local"]), "cloud": len(presence["cloud"]),
                     "error": node_presence.last_error()},
        "trace": decision.trace,
    }


@router.get("/rules")
async def read_rules() -> dict:
    """当前自定义规则链 + 类型注册表 + 按现行内置设置给出的种子建议（供前端一键生成）。"""
    bridge = get_settings().bridge
    suggest: dict = {
        "gen_type": {"kind": "gen_type", "params": {"gen_type": "video"},
                     "target": bridge.video_backend if bridge.video_backend in ("local", "modal") else "modal",
                     "name": "视频上云"},
    }
    if bridge.large_side_pixels > 0:
        suggest["min_pixels"] = {"kind": "min_pixels", "params": {"pixels": bridge.large_side_pixels},
                                 "target": "modal", "name": "大分辨率上云"}
    return {"rules": [r.model_dump() for r in bridge.rules], "kinds": RULE_KINDS, "suggest": suggest}


@router.put("/rules")
async def write_rules(body: RoutingRulesBody) -> dict:
    """整链替换自定义规则（先全量校验再落盘，非法即 422，不落半套）。"""
    try:
        cleaned = validate_rules(body.rules)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return update_bridge_section({"rules": cleaned})

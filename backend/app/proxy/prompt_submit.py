"""POST /prompt 核心拦截（规划文档 §2.1 / §3.1）：

解析 API 格式工作流图 → 分类（生图/视频）→ 规则决策 → 改写 prompt_id（桥接器生成，
兼容性已对本版 ComfyUI server.py 验证：接受规范小写 UUID）→ 图快照 + 落库 → 转发执行。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .. import cost as cost_svc
from ..core.config import get_settings
from ..logs import store
from ..models_mgr import cloud_index, local_index, node_presence
from ..models_mgr.presence import classify_model_refs
from ..routing.engine import classify_graph, decide, extract_primary_model
from ..runner import client as runner_client
from ..runner import console as runner_console
from ..runner import executor
from . import precheck
from .passthrough import get_upstream_client


def _extract_manual_backend(payload: dict) -> str | None:
    extra = payload.get("extra_data")
    if isinstance(extra, dict):
        val = extra.get("bridge_backend")
        if val in {"local", "modal"}:
            return val
    return None


def _extract_workflow_name(payload: dict) -> str | None:
    extra = payload.get("extra_data")
    if isinstance(extra, dict):
        pnginfo = extra.get("extra_pnginfo")
        if isinstance(pnginfo, dict):
            workflow = pnginfo.get("workflow")
            if isinstance(workflow, dict) and isinstance(workflow.get("name"), str):
                return workflow["name"]
        if isinstance(extra.get("workflow_name"), str):
            return extra["workflow_name"]
    return None


async def handle_prompt_submit(request: Request) -> Response:
    raw = await request.body()
    try:
        payload = json.loads(raw)
        assert isinstance(payload, dict)
    except Exception:
        # 非 JSON 载荷：交给本地 ComfyUI 原样报错，保持协议兼容
        return await _forward_raw(request, raw)

    prompt = payload.get("prompt")
    if not isinstance(prompt, dict) or not prompt:
        return await _forward_raw(request, raw)

    facts = classify_graph(prompt)

    # 模型引用归属：本地索引 vs 云端索引（快照，不做 IO；过期由后台重建保鲜）
    cloud_index.kick_refresh_if_stale()
    node_presence.kick_refresh_if_stale()  # 节点 class 两端在场（node_missing_* 规则数据源）
    await runner_client.ensure_state()  # Modal 应用部署状态保鲜（TTL 60s，过期才探一次 CLI）
    local_names = await local_index.local_names()
    cloud_only, missing = classify_model_refs(
        facts.get("model_refs") or [], local_names, cloud_index.snapshot()
    )
    budget = await cost_svc.abudget_state()  # 月度预算护栏现状（决策前采集，纯本地 SQL）
    decision = decide(
        facts,
        workflow_name=_extract_workflow_name(payload),
        manual_backend=request.headers.get("x-bridge-backend") or _extract_manual_backend(payload),
        models={"cloud_only": cloud_only, "missing": missing},
        budget=budget,
        presence=node_presence.snapshot(),
        cloud_active=await store.acount_active_modal(),  # 并发护栏计数（本请求尚未入库，不含自身）
    )
    if decision.backend == "modal" and budget.get("exceeded"):
        # 超限告警：护栏已把请求拉回本地/拒绝，控制台与 trace 双留痕（M4-1「并通知」）
        runner_console.log(f"[预算护栏] {decision.fallback}", "warning")

    # —— 提交前预校验（M3-5 余项）：目标端缺节点/缺模型提前拦截或自动供给（off = 历史行为）——
    preflight_mode = get_settings().bridge.preflight
    preflight_block_error: str | None = None
    preflight_options: list[dict] | None = None
    if preflight_mode in {"strict", "ask", "auto"} and decision.final_backend in {"local", "modal"}:
        pfr = precheck.scan(
            prompt,
            decision.final_backend,
            presence=node_presence.snapshot(),
            local_models=local_names,
            cloud_models=cloud_index.snapshot(),
        )
        action, options = precheck.decide_action(preflight_mode, pfr)
        if action == "auto_supply":
            supply = await precheck.auto_supply(pfr["supplyable"])
            pfr["auto_supply"] = supply
            precheck.mark_supply_done(pfr, supply["uploaded"])
            if supply["failed"]:
                # 上传失败的条目挪回缺失清单，与剩余缺节点一起按 ask 语义拦截
                precheck.mark_supply_failures(pfr, supply["failed"])
            if pfr["missing_nodes"] or pfr["missing_models"]:
                action, options = "block", precheck.block_options(pfr)
            else:
                action = "allow"  # 供给完成，放行执行
        decision.trace.append({
            "rule": "preflight",
            "mode": preflight_mode,
            "target": pfr["target"],
            "verdict": action,
            "missing_nodes": pfr["missing_nodes"],
            "missing_models": pfr["missing_models"],
            "cloud_only_models": pfr["cloud_only_models"],
            "supplyable_refs": pfr["supplyable_refs"],
            "auto_supply": pfr.get("auto_supply"),
        })
        if action == "block":
            preflight_block_error = precheck.blocked_message(pfr)
            preflight_options = options

    prompt_id = str(uuid.uuid4())
    payload["prompt_id"] = prompt_id
    graph_file, graph_hash = await store.asave_graph_snapshot(prompt_id, raw)

    record = {
        "prompt_id": prompt_id,
        "created_at": store.now_iso(),
        "created_ms": store.now_ms(),
        "backend": decision.backend,
        "final_backend": decision.final_backend,
        "gen_type": decision.gen_type,
        "workflow_name": _extract_workflow_name(payload),
        "model_name": extract_primary_model(prompt),
        "graph_hash": graph_hash,
        "graph_file": graph_file,
        "client_id": payload.get("client_id"),
        "status": "preflight" if preflight_block_error else "queued",
        "rule_trace": json.dumps(decision.trace, ensure_ascii=False),
        "error": preflight_block_error,
    }
    await store.ainsert_request(record)

    if preflight_block_error:
        # 预校验拦截：零执行零费用，strict 不带选项，ask/auto 附可操作选项
        content: dict = {
            "error": preflight_block_error,
            "prompt_id": prompt_id,
            "rule_trace": decision.trace,
        }
        if preflight_options is not None:
            content["options"] = preflight_options
        return JSONResponse(status_code=409, content=content)

    if decision.final_backend == "":
        # Modal 不可用且策略为 reject：不落地执行
        await store.aupdate_status(prompt_id, "error", decision.fallback)
        return JSONResponse(
            status_code=509,
            content={"error": decision.fallback, "prompt_id": prompt_id,
                     "rule_trace": decision.trace},
        )

    if decision.final_backend == "modal":
        # M1 Runner 通道：立即应答（prompt_id 即提交时的桥接 id），云端执行后台接力。
        # 冷启动/输入上传/输出回传都在执行器内完成，浏览器经 WS 收到映射后的进度事件。
        if executor.dispatch(prompt_id, payload):
            return JSONResponse(
                status_code=200,
                content={"prompt_id": prompt_id, "number": None, "node_errors": {}},
            )
        error = "云端执行器重复受理该请求"
        await store.aupdate_status(prompt_id, "error", error)
        return JSONResponse(status_code=500, content={"error": error, "prompt_id": prompt_id})

    # 云端专有模型且回落本地（Modal 通道未就绪）：本地校验必然拒绝（模型不在本地图清单），
    # 与其回 400 「value not in list」，不如明确留痕「待云端通道」。
    if decision.backend == "modal" and decision.final_backend == "local" and cloud_only:
        shown = "、".join(cloud_only[:5]) + ("…" if len(cloud_only) > 5 else "")
        error = (
            f"引用了云端专有模型 {shown}：仅存于 Modal 数据卷（本地无副本），"
            "而 Modal Runner 通道当前不可用（未部署或未配置 web_url）。"
            "可在「模型卷」页同步到本地后执行，或恢复通道后重试。"
        )
        await store.aupdate_status(prompt_id, "blocked", error)
        return JSONResponse(
            status_code=409,
            content={"error": error, "prompt_id": prompt_id,
                     "cloud_only_models": cloud_only, "rule_trace": decision.trace},
        )

    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"content-type": "application/json"}
    client = get_upstream_client()
    try:
        up = await client.post(
            f"{get_settings().local.comfy_url.rstrip('/')}/prompt", content=body, headers=headers
        )
    except httpx.HTTPError as exc:
        error = f"本地 ComfyUI 不可达: {exc}"
        await store.aupdate_status(prompt_id, "error", error)
        return JSONResponse(status_code=502, content={"error": error, "prompt_id": prompt_id})

    if up.status_code != 200:
        error = f"提交被 ComfyUI 拒绝（HTTP {up.status_code}）: {up.text[:300]}"
        await store.aupdate_status(prompt_id, "error", error)
    else:
        try:
            ack = up.json()
            node_errors = ack.get("node_errors") or {}
            if node_errors:
                await store.aupdate_status(prompt_id, "error", f"节点校验失败: {json.dumps(node_errors, ensure_ascii=False)[:300]}")
        except Exception:
            pass

    return Response(content=up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type", "application/json"))


async def _forward_raw(request: Request, raw: bytes) -> Response:
    client = get_upstream_client()
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in
               {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}]
    try:
        up = await client.request(request.method,
                                  f"{get_settings().local.comfy_url.rstrip('/')}{request.url.path}",
                                  headers=headers, content=raw)
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": f"本地 ComfyUI 不可达: {exc}"})
    return Response(content=up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type", "application/json"))

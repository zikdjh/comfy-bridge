"""路由决策引擎（规划文档 §3.1）：按序求值规则链，首条命中生效，决策链路全程留痕。

固定链（不参与 CRUD）：manual → tag → cloud_model → default。
自定义链（M3，bridge.rules）：非空时插在 cloud_model 与 default 之间按序求值，
并替换内置的 gen_type/size 两条（历史行为 = rules 为空时沿用内置两步）。
规则结构校验见 validate_rules；引擎对未知 kind/坏参数一律跳过并留痕（手编 config.toml 不致命）。

Modal 执行通道（app/runner）M1 已接入：可用性 = 凭证 + web_url + 应用已部署
（runner/client 缓存探测，prompt_submit 决策前保鲜）。不可用时按
bridge.on_modal_unavailable 策略回退本地（fallback_local）或拒绝（reject）。
预算护栏（M4-1）在可用性之前求值：决策目标 modal 且月度估算达上限
（cost.budget_state，调用方传入）时按 bridge.on_budget_exceed 回落本地或拒绝。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import get_settings
from ..core.constants import (
    IMAGE_OUTPUT_NODES,
    LATENT_SIZE_NODES,
    MODEL_FILE_EXTS,
    MODEL_INPUT_CATEGORIES,
    VIDEO_OUTPUT_NODES,
)
from ..runner import client as runner_client

# 自定义规则类型注册表：label/param_key/param_label 供设置页渲染与 API 文档共用
RULE_KINDS: dict[str, dict[str, str]] = {
    "workflow_tag": {"label": "工作流名前缀", "param_key": "prefix", "param_label": "前缀（不区分大小写）"},
    "gen_type": {"label": "生成类型", "param_key": "gen_type", "param_label": "类型（video/image）"},
    "min_pixels": {"label": "最小分辨率", "param_key": "pixels", "param_label": "图最大边 ≥（px）"},
}

_RULE_TARGETS = ("local", "modal")


def validate_rules(raw: list) -> list[dict]:
    """API 层规则链校验：逐条校验 kind/params/target，归一化后返回干净 dict 列表。

    id 缺失自动补（uuid 前 8 位）；enabled 缺省 True；校验失败抛 ValueError（API 转 422）。
    """
    if not isinstance(raw, list):
        raise ValueError("rules 必须是数组")  # noqa: TRY004 —— ValueError 是 API 422 的语义载体
    cleaned: list[dict] = []
    for i, item in enumerate(raw):
        pos = f"第 {i + 1} 条规则"
        if not isinstance(item, dict):
            raise ValueError(f"{pos}必须是对象")  # noqa: TRY004
        kind = str(item.get("kind") or "").strip()
        if kind not in RULE_KINDS:
            raise ValueError(f"{pos}类型无效：{kind or '(空)'}（可选：{'/'.join(RULE_KINDS)}）")
        target = str(item.get("target") or "").strip()
        if target not in _RULE_TARGETS:
            raise ValueError(f"{pos}目标无效：{target or '(空)'}（可选：{'/'.join(_RULE_TARGETS)}）")
        params = item.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError(f"{pos}参数必须是对象")  # noqa: TRY004
        if kind == "workflow_tag":
            prefix = str(params.get("prefix") or "").strip()
            if not prefix:
                raise ValueError(f"{pos}（工作流名前缀）前缀不能为空")
            params = {"prefix": prefix}
        elif kind == "gen_type":
            want = str(params.get("gen_type") or "").strip().lower()
            if want not in ("video", "image"):
                raise ValueError(f"{pos}（生成类型）只支持 video/image")
            params = {"gen_type": want}
        elif kind == "min_pixels":
            try:
                pixels = int(params.get("pixels"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValueError(f"{pos}（最小分辨率）阈值必须是整数") from None
            if pixels <= 0:
                raise ValueError(f"{pos}（最小分辨率）阈值必须大于 0（0 等于关闭，请直接停用规则）")
            params = {"pixels": pixels}
        cleaned.append({
            "id": str(item.get("id") or "").strip() or uuid.uuid4().hex[:8],
            "name": str(item.get("name") or "").strip(),
            "kind": kind,
            "params": params,
            "target": target,
            "enabled": bool(item.get("enabled", True)),
        })
    return cleaned


def extract_model_refs(prompt: dict) -> list[str]:
    """提取图内引用的模型文件名（已知模型输入名 + 未知 “*_name” 带模型扩展名兜底）。"""
    refs: set[str] = set()
    for node in (prompt or {}).values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, val in inputs.items():
            if not isinstance(val, str) or not val or val == "None":
                continue
            if key in MODEL_INPUT_CATEGORIES:
                refs.add(val)
            elif key.endswith("_name") and Path(val).suffix.lower() in MODEL_FILE_EXTS:
                refs.add(val)
    return sorted(refs)


def classify_graph(prompt: dict) -> dict:
    """解析 API 格式工作流图，产出分类事实：类型 / 输出节点 / 分辨率 / class / 模型引用。"""
    classes: set[str] = set()
    max_w = max_h = 0
    for node in (prompt or {}).values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str):
            classes.add(class_type)
        if class_type in LATENT_SIZE_NODES:
            inputs = node.get("inputs") or {}
            w, h = inputs.get("width"), inputs.get("height")
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                max_w, max_h = max(max_w, int(w)), max(max_h, int(h))
    video_nodes = sorted(classes & VIDEO_OUTPUT_NODES)
    image_nodes = sorted(classes & IMAGE_OUTPUT_NODES)
    gen_type = "video" if video_nodes else ("image" if image_nodes else "none")
    return {
        "gen_type": gen_type,
        "video_nodes": video_nodes,
        "image_nodes": image_nodes,
        "max_width": max_w or None,
        "max_height": max_h or None,
        "class_types": sorted(classes),
        "model_refs": extract_model_refs(prompt),
    }


def modal_available() -> bool:
    """Modal Runner 通道健康状态（读 runner/client 缓存，决策路径无 IO）。"""
    return runner_client.available()


@dataclass
class Decision:
    backend: str  # 决策目标（未应用回退）
    final_backend: str  # 回退后实际执行
    gen_type: str
    fallback: str | None = None  # 回退/拒绝原因
    trace: list[dict] = field(default_factory=list)

    def trace_record(self) -> dict:
        return {"backend": self.backend, "final_backend": self.final_backend, "trace": self.trace}


def decide(
    facts: dict,
    workflow_name: str | None = None,
    manual_backend: str | None = None,
    models: dict | None = None,
    budget: dict | None = None,
) -> Decision:
    """models: 模型引用归属 {"cloud_only": [...], "missing": [...]}（由调用方按本地/云端索引判定）。
    budget: 预算护栏现状 {"exceeded": bool, "used_usd": float, "monthly_usd": float}
    （cost.budget_state()，由调用方在决策前计算；None = 未启用/未采集）。"""
    settings = get_settings().bridge
    trace: list[dict] = []

    def step(rule: str, matched: bool, detail: str, target: str | None = None) -> bool:
        trace.append({"rule": rule, "matched": matched, "detail": detail, "target": target})
        return matched

    decision: str | None = None

    # 1. manual：提交方显式指定，最高优先级
    if manual_backend in {"local", "modal"}:
        if step("manual", True, f"提交方显式指定 backend={manual_backend}", manual_backend):
            decision = manual_backend
    else:
        step("manual", False, "未指定 x-bridge-backend 头或 bridge_backend 元数据")

    # 2. tag：工作流名 cloud: 前缀
    if decision is None:
        if workflow_name and workflow_name.strip().lower().startswith("cloud:"):
            if step("tag", True, f'工作流名 "{workflow_name}" 命中 cloud: 前缀', "modal"):
                decision = "modal"
        else:
            step("tag", False, "工作流名未带 cloud: 前缀" if workflow_name else "未获取到工作流名")

    # 3. cloud_model：引用了云端专有模型（本地无副本）→ 本地校验必然拒绝，必须 modal
    if decision is None:
        cloud_only = (models or {}).get("cloud_only") or []
        if cloud_only:
            shown = "、".join(cloud_only[:3]) + ("…" if len(cloud_only) > 3 else "")
            if step("cloud_model", True, f"云端专有模型 {shown} 本地无副本 → modal", "modal"):
                decision = "modal"
        else:
            step("cloud_model", False, "未引用云端专有模型")

    # 4. 自定义规则链（bridge.rules 非空时替换内置 gen_type/size 两步）
    custom_rules = list(getattr(settings, "rules", None) or [])
    if not custom_rules:
        # 4a. gen_type：视频类节点（内置默认行为，rules 非空时由自定义链接管）
        if decision is None:
            if facts["gen_type"] == "video" and settings.video_backend == "modal":
                detail = f"视频类输出节点 {facts['video_nodes']} → video_backend=modal"
                if step("gen_type", True, detail, "modal"):
                    decision = "modal"
            elif facts["gen_type"] == "video":
                step("gen_type", False, f"视频类请求，但 video_backend={settings.video_backend}")
            else:
                step("gen_type", False, "未检出视频类输出节点")

        # 5. size：大分辨率
        if decision is None:
            side = max((facts["max_width"] or 0), (facts["max_height"] or 0)) or None
            threshold = settings.large_side_pixels
            if threshold > 0 and side and side >= threshold:
                detail = f"图最大边 {side}px ≥ 阈值 {threshold}px → modal"
                if step("size", True, detail, "modal"):
                    decision = "modal"
            else:
                step("size", False, f"最大边 {side or '未知'}px，阈值 {threshold or '关闭'}")
    else:
        for r in custom_rules:
            if decision is not None:
                break
            label = f"custom:{r.name or r.kind}"
            if not r.enabled:
                step(label, False, "规则已停用，跳过")
                continue
            params = r.params or {}
            matched, detail = False, ""
            if r.kind == "workflow_tag":
                prefix = str(params.get("prefix") or "")
                name = (workflow_name or "").strip()
                matched = bool(prefix and name.lower().startswith(prefix.lower()))
                detail = f'工作流名 "{name or "未知"}" 判断前缀 "{prefix}"（不区分大小写）'
            elif r.kind == "gen_type":
                want = str(params.get("gen_type") or "")
                matched = facts["gen_type"] == want
                detail = f"图类型 {facts['gen_type']} 判断 {want}（输出节点 {facts['video_nodes'] or facts['image_nodes']}）"
            elif r.kind == "min_pixels":
                side = max((facts["max_width"] or 0), (facts["max_height"] or 0)) or 0
                threshold = int(params.get("pixels") or 0)
                matched = threshold > 0 and side >= threshold
                detail = f"图最大边 {side or '未知'}px 判断 ≥ {threshold}px"
            else:
                step(label, False, f"未知规则类型 {r.kind or '(空)'}，跳过（请检查 config.toml）")
                continue
            if matched and r.target in _RULE_TARGETS:
                if step(label, True, detail, r.target):
                    decision = r.target
            else:
                step(label, False, detail + ("（目标无效，视为未命中）" if matched else ""))

    # 6. default 兜底
    if decision is None:
        decision = settings.default_backend if settings.default_backend in {"local", "modal"} else "local"
        step("default", True, f"无规则命中，取 default_backend={decision}", decision)

    final_backend, fallback = decision, None
    if decision == "modal" and (budget or {}).get("exceeded"):
        # 预算护栏先于可用性求值：超限是策略决定，无论通道健康与否都不再上云
        used = float((budget or {}).get("used_usd") or 0)
        limit = float((budget or {}).get("monthly_usd") or 0)
        if settings.on_budget_exceed == "reject":
            fallback = (
                f"本月云端估算 ${used:.2f} 已达预算上限 ${limit:.2f}，"
                "on_budget_exceed=reject 拒绝执行"
            )
            final_backend = ""  # 拒绝执行，不落地
        else:
            fallback = (
                f"本月云端估算 ${used:.2f} 已达预算上限 ${limit:.2f}，"
                "按 on_budget_exceed=fallback_local 回落本地"
            )
            final_backend = "local"
        trace.append({"rule": "budget_guardrail", "matched": True, "detail": fallback, "target": final_backend or None})
    elif decision == "modal" and not modal_available():
        if settings.on_modal_unavailable == "reject":
            fallback = "Modal 通道不可用且 on_modal_unavailable=reject"
            final_backend = ""  # 拒绝执行，不落地
        else:
            fallback = f"Modal 通道不可用（{runner_client.deployed_state() or '未部署/未配置'}），按 on_modal_unavailable=fallback_local 回退"
            final_backend = "local"
        trace.append({"rule": "fallback", "matched": True, "detail": fallback, "target": final_backend or None})

    return Decision(backend=decision, final_backend=final_backend, gen_type=facts["gen_type"],
                    fallback=fallback, trace=trace)

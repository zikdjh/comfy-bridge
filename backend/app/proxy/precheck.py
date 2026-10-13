"""提交前预校验（M3-5 余项）：在请求下发前按目标端复刻 validate_prompt 的关键检查。

数据源全部复用路由决策的缓存（本模块自身不做索引 IO）：
- 节点 class：models_mgr.node_presence 两端 /object_info 在场缓存（TTL 单飞 + 持久化）
- 模型文件：local_index（本地目录扫描）+ cloud_index（Modal 卷 models/ 清单缓存）

供给策略 bridge.preflight（规划「strict / ask / auto」）：
- off    不校验（历史行为不变，默认）
- strict 缺节点/缺模型 → 拦截（409，blocked 留痕，不带选项）
- ask    拦截并附结构化可操作选项（上传哪些模型 / 安装哪些节点 / 改走云端）
- auto   缺模型且本地有副本 → 自动上传云端卷后放行；缺节点/本地目标仍按 ask 拦截

未知取值一律按 off 处理（配置打错不改变历史行为）。
"""

from __future__ import annotations

from pathlib import Path

from ..core.config import model_dirs_resolved
from ..core.constants import MODEL_FILE_EXTS, MODEL_INPUT_CATEGORIES
from ..modal_volume import client as volume_cli
from ..modal_volume.client import ModalCliError
from ..runner import console as runner_console


def extract_ref_categories(prompt: dict) -> list[tuple[str, str | None]]:
    """图内模型引用 → [(ref, 分类|None)]。

    已知模型输入名（MODEL_INPUT_CATEGORIES）给规范分类（取元组首位）；
    未知 “*_name” 带模型扩展名的兜底引用无分类（可判存在性，不可自动供给）。
    """
    out: set[tuple[str, str | None]] = set()
    for node in (prompt or {}).values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, val in inputs.items():
            if not isinstance(val, str) or not val or val == "None":
                continue
            cats = MODEL_INPUT_CATEGORIES.get(key)
            if cats:
                out.add((val, cats[0]))
            elif key.endswith("_name") and Path(val).suffix.lower() in MODEL_FILE_EXTS:
                out.add((val, None))
    return sorted(out)


def scan(
    prompt: dict,
    target: str,
    *,
    presence: dict[str, set[str]] | None,
    local_models: dict[str, set[str]],
    cloud_models: dict[str, set[str]],
) -> dict:
    """目标端（local|modal）缺节点/缺模型扫描。纯函数：只读缓存快照，不做 IO。

    presence 端集合为空 = 索引不可用（服务离线/未刷新），节点校验降级跳过并在 note 说明。
    """
    side = "cloud" if target == "modal" else "local"  # 后端名 → node_presence 缓存键
    classes: set[str] = set()
    for node in (prompt or {}).values():
        if isinstance(node, dict):
            class_type = node.get("class_type")
            if isinstance(class_type, str) and class_type:
                classes.add(class_type)
    known = (presence or {}).get(side) or set()
    missing_nodes = sorted(classes - known) if known else []
    note = None if known else f"{target} 端节点在场索引为空，已跳过节点校验"

    local_flat = set().union(*local_models.values()) if local_models else set()
    cloud_flat = set().union(*cloud_models.values()) if cloud_models else set()

    missing_models: list[str] = []
    supplyable: list[tuple[str, str | None]] = []
    cloud_only: list[str] = []
    for ref, cat in extract_ref_categories(prompt):
        if target == "modal":
            if ref in cloud_flat:
                continue
            if ref in local_flat:
                supplyable.append((ref, cat))  # 云端缺、本地有副本 → auto 可上传
            else:
                missing_models.append(ref)
        else:  # local
            if ref in local_flat:
                continue
            if ref in cloud_flat:
                cloud_only.append(ref)  # 云端专有：本地执行必然失败，给改道/同步提示
            else:
                missing_models.append(ref)

    return {
        "ok": not missing_nodes and not missing_models and not cloud_only,
        "target": target,
        "missing_nodes": missing_nodes,
        "missing_models": missing_models,
        "supplyable": supplyable,
        "supplyable_refs": sorted(r for r, _ in supplyable),
        "cloud_only_models": cloud_only,
        "note": note,
    }


def decide_action(mode: str, result: dict) -> tuple[str, list[dict] | None]:
    """供给策略 → (动作, 选项)。动作 ∈ allow | auto_supply | block。

    off 恒 allow；ok 恒 allow。auto：目标=modal 且缺失模型全部可本地供给时返回 auto_supply
    （即使同时缺节点——先上传，剩余问题由调用方供给后按 ask 语义拦截）；strict 拦截不附选项；
    ask 拦截附选项。未知取值按 off（配置打错不改变历史行为）。
    """
    if mode not in {"strict", "ask", "auto"} or result["ok"]:
        return "allow", None
    if result["note"]:  # 索引不可用：无法可靠判定，放行交由目标端权威校验
        return "allow", None
    options = block_options(result)
    if mode == "auto" and result["target"] == "modal" \
            and not result["missing_models"] and result["supplyable"]:
        return "auto_supply", None
    if mode == "strict":
        return "block", None
    return "block", options  # ask，以及 auto 的不可自动修复场景


def mark_supply_done(result: dict, uploaded: list[str]) -> None:
    """上传成功的条目从 supplyable 清单移除（消息/选项不再提示可上传）。"""
    done = set(uploaded)
    result["supplyable"] = [it for it in result["supplyable"] if it[0] not in done]
    result["supplyable_refs"] = sorted(r for r, _ in result["supplyable"])


def mark_supply_failures(result: dict, failed: list[dict]) -> None:
    """auto 供给后仍有失败条目：从 supplyable 挪进 missing_models，按 ask 语义拦截。"""
    failed_refs = {f["ref"] for f in failed}
    result["supplyable"] = [it for it in result["supplyable"] if it[0] not in failed_refs]
    result["supplyable_refs"] = sorted(r for r, _ in result["supplyable"])
    result["missing_models"] = sorted(set(result["missing_models"]) | failed_refs)
    result["ok"] = False


def block_options(result: dict) -> list[dict]:
    """ask/auto 拦截时附带的可操作选项（供上层 GUI 或人工决策）。"""
    opts: list[dict] = []
    if result["missing_nodes"]:
        opts.append({"action": "install_nodes", "nodes": result["missing_nodes"],
                     "hint": "到「节点管理」页安装/同步后重试；刚装过可稍候在场缓存刷新"})
    if result["supplyable_refs"]:
        opts.append({"action": "upload_to_volume", "models": result["supplyable_refs"],
                     "hint": "本地有副本、云端卷缺失；切 auto 模式可自动上传，或在「模型卷」页上传"})
    if result["missing_models"]:
        opts.append({"action": "missing_models", "models": result["missing_models"],
                     "hint": "两端索引均未找到该模型文件，请确认文件名与目录映射"})
    if result["cloud_only_models"]:
        opts.append({"action": "reroute_modal", "models": result["cloud_only_models"],
                     "hint": "模型仅存于 Modal 数据卷：手动指定云端后端执行，或在「模型卷」页同步到本地"})
    return opts


def blocked_message(result: dict) -> str:
    """拦截文案（写日志 error 字段 + 响应 error，ComfyUI 前端原样展示）。"""
    side = "云端（Modal）" if result["target"] == "modal" else "本地"
    parts: list[str] = []
    if result["missing_nodes"]:
        shown = "、".join(result["missing_nodes"][:5]) + ("…" if len(result["missing_nodes"]) > 5 else "")
        parts.append(f"{side}缺少节点类型 {shown}")
    if result["cloud_only_models"]:
        shown = "、".join(result["cloud_only_models"][:5]) + ("…" if len(result["cloud_only_models"]) > 5 else "")
        parts.append(f"引用了云端专有模型 {shown}（本地无副本）")
    if result["missing_models"]:
        shown = "、".join(result["missing_models"][:5]) + ("…" if len(result["missing_models"]) > 5 else "")
        parts.append(f"两端都找不到模型文件 {shown}")
    tail = "。提交已被预校验拦截（未产生任何执行与费用）"
    if result["supplyable_refs"]:
        tail += f"；其中 {len(result['supplyable_refs'])} 个模型本地有副本，可切 auto 模式自动上传"
    return "；".join(parts) + tail


async def auto_supply(items: list[tuple[str, str | None]]) -> dict:
    """把本地有副本的缺失模型上传云端卷 models/<分类>/<ref>（auto 供给）。

    逐个上传：单个失败不阻塞其余；本地找不到文件或无分类映射的条目记为失败。
    全部经 SDK 网关 put_file（免容器，与节点同步同一原语）。
    """
    dirs = model_dirs_resolved()
    uploaded: list[str] = []
    failed: list[dict] = []
    for ref, cat in items:
        if not cat or cat not in dirs:
            failed.append({"ref": ref, "error": "无法确定模型分类目录"})
            continue
        local_path = Path(dirs[cat]) / ref
        if not local_path.is_file():
            failed.append({"ref": ref, "error": f"本地副本不存在：{local_path}"})
            continue
        remote = f"models/{cat}/{ref}"
        try:
            await volume_cli.put_file(str(local_path), remote, force=True)
        except ModalCliError as exc:
            failed.append({"ref": ref, "error": str(exc)[:200]})
            continue
        uploaded.append(ref)
        runner_console.log(f"[预校验] 自动供给：{ref} → 数据卷 {remote}")
    return {"uploaded": uploaded, "failed": failed}

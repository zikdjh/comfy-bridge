"""GET /object_info 合并：本地 class_type + 云端模型的策略化合并。

前端（ComfyUI 编辑器）的模型下拉与缺失高亮完全由 /object_info 驱动。桥接器把
云端数据卷里的模型名补进本地组合框，用户可直接选中云端模型、不再出现缺失红线；
排队时由路由层（cloud_model 规则）改道 Modal。

合并策略（bridge.object_info_merge）：

- local-first（默认）：同名 class_type 完全使用本地 schema，只追加云端独有节点；
- union：同名 class_type 保留本地 schema，但把云端独有的模型组合项并入本地 schema；
- local-only：不合并云端模型名，也不追加云端节点（排障用）。

云端 class_type schema 不会在 /object_info 请求内实时拉取，否则每次出图都可能触发
Modal 冷启动计费。schema 由节点页「对比云端 class_type」显式刷新后缓存；缓存同时
记录与本地冲突的同名 class_type，供 /api/v1/nodes/diff 展示。
"""

from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ..core.config import get_settings
from ..core.constants import MODEL_INPUT_CATEGORIES
from ..models_mgr import cloud_index
from .passthrough import get_upstream_client

logger = logging.getLogger(__name__)

_VALID_STRATEGIES = {"local-first", "union", "local-only"}
_cache: dict[str, tuple[bytes, tuple, bytes, float]] = {}

# /object_info 热缓存 TTL（秒）。TTL 内直接返回上次的合并结果，不再等上游
# （大注册表下上游响应本身就 > 200ms，是数据面延迟的主要来源）。
# invalidate_merge_cache()（策略切换/云端 schema 刷新）会主动清空。
_TTL_SECONDS = 10.0

# 云端 class_type schema 缓存；由节点页显式刷新，不在 /object_info 请求路径触发冷启动。
_cloud_schemas: dict[str, dict] = {}
_cloud_conflicts: dict[str, list[str]] = {}
_cloud_schema_stamp: tuple[float, int] | None = None


def _strategy() -> str:
    value = (get_settings().bridge.object_info_merge or "").strip().lower()
    return value if value in _VALID_STRATEGIES else "local-first"


def effective_strategy() -> str:
    """当前实际生效的合并策略；配置值非法时回退 local-first。"""
    return _strategy()


def _merge_model_names(doc: dict) -> None:
    """把云端索引里的模型名追加到本地模型组合框（已知 MODEL_INPUT_CATEGORIES）。"""
    cloud = cloud_index.snapshot()
    if not cloud:
        return

    def names_for(cats: tuple[str, ...]) -> set[str]:
        names: set[str] = set()
        for cat in cats:
            names |= cloud.get(cat, set())
        return names

    for ndef in doc.values():
        if not isinstance(ndef, dict):
            continue
        inputs = ndef.get("input")
        if not isinstance(inputs, dict):
            continue
        for section in ("required", "optional"):
            block = inputs.get(section)
            if not isinstance(block, dict):
                continue
            for key, spec in block.items():
                cats = MODEL_INPUT_CATEGORIES.get(key)
                if cats is None or not isinstance(spec, list) or not spec:
                    continue
                options = spec[0]
                if not isinstance(options, list):
                    continue
                extra = sorted(n for n in names_for(cats) if n not in options)
                if extra:
                    spec[0] = [*options, *extra]


def _union_schema(local: dict, remote: dict) -> dict:
    """保留本地 schema，把云端 schema 中的模型组合项并入同名输入。"""
    merged = deepcopy(local)
    local_inputs = merged.get("input")
    remote_inputs = remote.get("input")
    if not isinstance(local_inputs, dict) or not isinstance(remote_inputs, dict):
        return merged
    for section in ("required", "optional"):
        local_block = local_inputs.get(section)
        remote_block = remote_inputs.get(section)
        if not isinstance(local_block, dict) or not isinstance(remote_block, dict):
            continue
        for key, remote_spec in remote_block.items():
            if key not in MODEL_INPUT_CATEGORIES:
                continue
            local_spec = local_block.get(key)
            if not isinstance(local_spec, list) or not local_spec:
                continue
            if not isinstance(remote_spec, list) or not remote_spec:
                continue
            local_options, remote_options = local_spec[0], remote_spec[0]
            if not isinstance(local_options, list) or not isinstance(remote_options, list):
                continue
            extra = [v for v in remote_options if v not in local_options]
            if extra:
                local_spec[0] = [*local_options, *extra]
    return merged


def _merge_doc(doc: dict, *, add_cloud_only: bool = True) -> dict:
    strategy = _strategy()
    if strategy == "local-only":
        return doc
    _merge_model_names(doc)
    if not _cloud_schemas:
        return doc

    out = dict(doc)
    for name, remote in _cloud_schemas.items():
        local = out.get(name)
        if not isinstance(local, dict):
            if add_cloud_only:
                out[name] = deepcopy(remote)
        elif strategy == "union":
            out[name] = _union_schema(local, remote)
    return out


def _install_cloud_schemas(remote_doc: dict, conflicts: dict[str, list[str]]) -> None:
    global _cloud_schemas, _cloud_conflicts, _cloud_schema_stamp
    remote_doc = remote_doc if isinstance(remote_doc, dict) else {}
    _cloud_schemas = {
        str(name): value for name, value in remote_doc.items() if isinstance(value, dict)
    }
    _cloud_conflicts = {
        str(name): [str(path) for path in paths]
        for name, paths in conflicts.items()
        if isinstance(paths, list)
    }
    _cloud_schema_stamp = (time.time(), len(_cloud_schemas))


def set_cloud_schemas(remote_doc: dict, local_doc: dict) -> dict:
    """缓存云端 schema，并计算与本地同名 class_type 的差异路径。"""
    remote_doc = remote_doc if isinstance(remote_doc, dict) else {}
    local_doc = local_doc if isinstance(local_doc, dict) else {}
    conflicts: dict[str, list[str]] = {}
    remote_schemas = {
        str(name): value for name, value in remote_doc.items() if isinstance(value, dict)
    }
    for name in sorted(set(remote_schemas) & set(local_doc)):
        local = local_doc.get(name)
        remote = remote_schemas[name]
        if not isinstance(local, dict):
            continue
        diffs = _diff_paths(local, remote)
        if diffs:
            conflicts[name] = diffs[:20]
    _install_cloud_schemas(remote_schemas, conflicts)
    logger.info(
        "云端 object_info schema 已刷新：%d 个 class_type，同名冲突 %d 个",
        len(_cloud_schemas),
        len(conflicts),
    )
    return {"count": len(_cloud_schemas), "conflicts": conflicts}


def restore_cloud_schemas(remote_doc: dict, conflicts: dict[str, list[str]] | None = None) -> None:
    """从持久缓存恢复云端 schema，不重新请求云端。"""
    _install_cloud_schemas(remote_doc, conflicts or {})


def cloud_schemas_snapshot() -> dict:
    """当前云端 schema 的副本，供节点差异缓存持久化。"""
    return deepcopy(_cloud_schemas)


def invalidate_merge_cache() -> None:
    """策略切换 / 云端 schema 刷新后立即失效 /object_info 合并缓存。"""
    _cache.clear()


def _diff_paths(local: Any, remote: Any, prefix: str = "") -> list[str]:
    """递归比较 JSON 结构，返回前若干条有差异的叶子路径。"""
    if type(local) is not type(remote):
        return [prefix or "<root>"]
    if isinstance(local, dict):
        out: list[str] = []
        for key in sorted(set(local) | set(remote)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in local or key not in remote:
                out.append(path)
            else:
                out.extend(_diff_paths(local[key], remote[key], path))
            if len(out) >= 20:
                break
        return out[:20]
    if isinstance(local, list):
        if len(local) != len(remote):
            return [prefix or "<root>"]
        for index, (left, right) in enumerate(zip(local, remote)):
            if _diff_paths(left, right, f"{prefix}[{index}]"):
                # combo 选项列表常以 [值, {元数据}] 形式出现；折叠到字段路径，
                # 避免诊断里出现 ckpt_name[0][0] 这类过深、对用户无意义的索引。
                return [prefix or "<root>"]
        return []
    return [] if local == remote else [prefix or "<root>"]


async def handle_object_info(request: Request, node_type: str | None) -> Response:
    now = time.monotonic()
    stamp = (cloud_index.stamp(), _strategy(), _cloud_schema_stamp)
    hit = _cache.get(request.url.path)
    if hit is not None and hit[1] == stamp and (now - hit[3]) < _TTL_SECONDS:
        content_type = "application/json"
        return Response(hit[2], media_type=content_type)

    base = get_settings().local.comfy_url.rstrip("/")
    url = f"{base}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    client = get_upstream_client()
    try:
        up = await client.get(url, timeout=20.0)  # 共享客户端 read=None，这里必须显式限时
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": f"本地 ComfyUI 不可达: {exc}"})

    if up.status_code != 200:
        content_type = up.headers.get("content-type", "application/json")
        return Response(up.content, status_code=up.status_code, media_type=content_type)
    content_type = up.headers.get("content-type", "application/json")
    upstream = up.content

    strategy = _strategy()
    if hit is not None and hit[0] == upstream and hit[1] == stamp:
        return Response(hit[2], media_type=content_type)

    try:
        doc = json.loads(upstream)
    except (TypeError, ValueError):
        _cache[request.url.path] = (upstream, stamp, upstream, now)
        return Response(upstream, media_type=content_type)
    if not isinstance(doc, dict):
        _cache[request.url.path] = (upstream, stamp, upstream, now)
        return Response(upstream, media_type=content_type)

    if strategy != "local-only" and (cloud_index.stamp()[1] > 0 or _cloud_schemas):
        doc = _merge_doc(doc, add_cloud_only=node_type is None)
        merged = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
        logger.info(
            "object_info 已合并（%s）：策略 %s，云端模型索引 %d 项，云端 schema %d 个",
            node_type or "全量",
            strategy,
            cloud_index.stamp()[1],
            len(_cloud_schemas),
        )
    else:
        merged = upstream
    _cache[request.url.path] = (upstream, stamp, merged, now)
    return Response(merged, media_type=content_type)

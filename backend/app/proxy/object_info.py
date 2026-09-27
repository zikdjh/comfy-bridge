"""GET /object_info 合并：把 Modal 数据卷上「云端专有」的模型追加进本地组合框清单。

前端（ComfyUI 编辑器）的模型下拉与缺失高亮完全由 /object_info 驱动 —— 追加云端清单后，
用户可直接选中云端模型、不再出现缺失红线；排队时由路由层（cloud_model 规则）改道 Modal，
本地 ComfyUI 零改动、零感知。合并仅限已知模型输入名（MODEL_INPUT_CATEGORIES），保守不误伤。

缓存：上游响应字节 + 云端索引版本（stamp）任一未变则直接复用合并结果。
"""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ..core.config import get_settings
from ..core.constants import MODEL_INPUT_CATEGORIES
from ..models_mgr import cloud_index
from .passthrough import get_upstream_client

logger = logging.getLogger(__name__)

# path → (上游响应字节, 云端索引 stamp, 合并后字节)
_cache: dict[str, tuple[bytes, tuple, bytes]] = {}


def _merge_doc(doc: dict) -> dict:
    cloud = cloud_index.snapshot()
    if not cloud:
        return doc

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
    return doc


async def handle_object_info(request: Request, node_type: str | None) -> Response:
    base = get_settings().local.comfy_url.rstrip("/")
    url = f"{base}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    client = get_upstream_client()
    try:
        up = await client.get(url, timeout=20.0)  # 共享客户端 read=None，这里必须显式限时
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": f"本地 ComfyUI 不可达: {exc}"})

    content_type = up.headers.get("content-type", "application/json")
    if up.status_code != 200:
        return Response(up.content, status_code=up.status_code, media_type=content_type)
    upstream = up.content

    stamp = cloud_index.stamp()
    hit = _cache.get(request.url.path)
    if hit is not None and hit[0] == upstream and hit[1] == stamp:
        return Response(hit[2], media_type=content_type)

    try:
        doc = json.loads(upstream)
        assert isinstance(doc, dict)
    except Exception:
        _cache[request.url.path] = (upstream, stamp, upstream)
        return Response(upstream, media_type=content_type)

    if stamp[1] > 0:
        doc = _merge_doc(doc)
        merged = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
        logger.info("object_info 已合并云端模型清单（%s）：索引 %d 项", node_type or "全量", stamp[1])
    else:
        merged = upstream
    _cache[request.url.path] = (upstream, stamp, merged)
    return Response(merged, media_type=content_type)

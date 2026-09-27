"""请求日志 API：拦截层落库记录的查询（规划文档 §5 /api/requests）。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query

from ...logs import store

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("")
async def list_requests(
    limit: int = Query(default=50, ge=1, le=500),
    backend: str = Query(default="all"),
    status: str = Query(default="all"),
) -> dict:
    items = await asyncio.to_thread(store.list_requests, limit, backend, status)
    return {"total": len(items), "items": items}


@router.get("/{req_id}")
async def get_request(req_id: int) -> dict:
    item = await asyncio.to_thread(store.get_request, req_id)
    if item is None:
        raise HTTPException(status_code=404, detail="请求不存在")
    return item


@router.get("/{req_id}/graph")
async def get_request_graph(req_id: int) -> dict:
    """返回拦截时快照的工作流图 JSON（提交载荷原文）。"""
    item = await asyncio.to_thread(store.get_request, req_id)
    if item is None or not item.get("graph_file"):
        raise HTTPException(status_code=404, detail="图快照不存在")
    graph_path = store.GRAPH_DIR / item["graph_file"]
    if not graph_path.is_file():
        raise HTTPException(status_code=404, detail="图快照文件已清理")
    return json.loads(graph_path.read_text(encoding="utf-8"))

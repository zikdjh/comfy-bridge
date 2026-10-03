"""同步任务 API（M2-6 余项）：任务化同步作业的查询/取消面。

GET  /api/v1/jobs              最近作业列表（新→旧，默认 50 条）+ 运行中计数
GET  /api/v1/jobs/{id}         作业详情（进度条目 + 日志）
POST /api/v1/jobs/{id}/cancel  协作式取消（当前条目完成后停止）

作业体在 sync/jobs.py 注册表内运行（内存态，保留最近 100 个）；镜像重建/卷移动/
URL 直下作业保持各自既有 API 面不变，不走这里。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ...sync import jobs

router = APIRouter()


@router.get("/jobs")
async def list_jobs(limit: int = Query(50, ge=1, le=100)) -> dict:
    return {"jobs": jobs.list_jobs(limit), "running": jobs.running_count()}


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="作业不存在")
    return {"job": job}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    if jobs.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="作业不存在")
    return {"cancelled": jobs.request_cancel(job_id)}

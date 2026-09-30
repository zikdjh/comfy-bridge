"""Modal 账单实时查询（M4-1 补充：账户级真实消耗，与本地按次估算互补）。

沿用「后端主进程不 import modal SDK」的边界：由 `sys.executable -c` 子进程
内 import modal（依赖已声明，见 pyproject），用其 gRPC 客户端调
WorkspaceBillingSummary，stdout 输出一行 JSON；凭证经 MODAL_TOKEN_ID/SECRET
环境变量注入（auth_env，与 modal CLI 子进程同路径），未注入时 from_env()
回落 ~/.modal.toml。

口径：gRPC 语义为 [start_timestamp, start+1 个月)，按 UTC 自然月对齐——
Modal 计量按 UTC 结算，与本地日界的估算聚合存在时区差异。无公开的
「剩余积分总额」接口（plan 总额仅仪表盘会话可见），故展示本期计量消耗 /
积分抵扣 / 实际计费；实际计费 > 0 即赠金（或额度）已耗尽。

缓存：成功 120s、失败 20s（前端 30s 轮询，避免连续拉起子进程）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from ..modal_volume.client import auth_env

_TTL_OK = 120.0
_TTL_FAIL = 20.0
_CHILD_TIMEOUT = 45.0

# 子进程脚本：无参数、stdin 关闭；stdout 仅一行 JSON；任何异常（含 import 失败、
# 网络错误）都转成 ok=false 的 JSON 而非非零退出，父进程统一走 parse_output。
_CHILD_SCRIPT = r'''
import asyncio, json, os
from datetime import datetime, timezone


def _f(text):
    try:
        return round(float(text), 4)
    except (TypeError, ValueError):
        return None


def main():
    try:
        import modal
        from modal_proto import api_pb2
        from google.protobuf.timestamp_pb2 import Timestamp

        client = modal.Client.from_env()  # 阻塞构造，须在事件循环创建之前
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ts = Timestamp()
        ts.FromDatetime(start)
        resp = asyncio.run(client.stub.WorkspaceBillingSummary(
            api_pb2.WorkspaceBillingSummaryRequest(start_timestamp=ts), retry=None, timeout=30))
        adjustments = dict(resp.adjustments)
        breakdown = {}
        for key, value in resp.metered_cost_breakdown.items():
            num = _f(value)
            if num:
                breakdown[key] = num
        print(json.dumps({
            "ok": True,
            "period_start": resp.start_timestamp.ToDatetime(timezone.utc).strftime("%Y-%m-%d"),
            "period_end": resp.end_timestamp.ToDatetime(timezone.utc).strftime("%Y-%m-%d"),
            "metered_cost_usd": _f(resp.metered_cost),
            "billed_cost_usd": _f(resp.billed_cost),
            "credits_applied_usd": abs(_f(adjustments.get("Credits")) or 0.0),
            "plan_cost_usd": _f(adjustments.get("Plan Cost")) or 0.0,
            "breakdown": dict(sorted(breakdown.items(), key=lambda kv: -(kv[1] or 0.0))),
        }, ensure_ascii=False))
    except BaseException as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}))


main()
'''


def parse_output(raw: bytes) -> dict:
    """子进程 stdout → 账单 dict；任何解析异常归为 ok=false（不抛出）。"""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace").strip() or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"账单子进程输出解析失败: {exc}"[:300]}
    if isinstance(data, dict) and data.get("ok") is True:
        return data
    err = data.get("error") if isinstance(data, dict) else None
    return {"ok": False, "error": str(err or "账单子进程返回异常数据")[:300]}


_cache: dict = {"at": -1e9, "data": None}


def _cache_fresh(now: float) -> bool:
    ttl = _TTL_OK if (_cache["data"] or {}).get("ok") else _TTL_FAIL
    return now - _cache["at"] < ttl


async def fetch(force: bool = False) -> dict:
    """本月 Modal 账单；带缓存，永不抛出（失败转 ok=false + error）。"""
    now = time.monotonic()
    if not force and _cache["data"] is not None and _cache_fresh(now):
        return _cache["data"]
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", _CHILD_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONWARNINGS": "ignore", **auth_env()},
        )
    except OSError as exc:
        data = {"ok": False, "error": f"无法启动账单子进程: {exc}"[:300]}
    else:
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_CHILD_TIMEOUT)
        except TimeoutError:
            proc.kill()
            data = {"ok": False, "error": f"Modal 账单查询超时（>{_CHILD_TIMEOUT:g}s）"}
        else:
            data = parse_output(out)
    _cache.update(at=now, data=data)
    return data

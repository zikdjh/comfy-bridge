"""Modal 账单实时查询（M4-1 补充：账户级真实消耗，与本地按次估算互补）。

优先官方封装：modal CLI `billing summary`（1.5+，即 SDK `Workspace.billing.summary`
的官方前端）——与 volume/app 等一致走 CLI 子进程，凭证经 MODAL_TOKEN_ID/SECRET
环境变量注入（auth_env，未配置时 CLI 自动回落 ~/.modal.toml），后端主进程
不 import modal SDK。曾用 modal_proto stub 直调 WorkspaceBillingSummary，
已替换：proto 面是内部生成物，官方文档口径是 CLI / Workspace.billing。

口径：单月窗口，对齐 UTC 月界（cycle=YYYY-MM）；metered_cost = Modal 计量
（含 Deployed/Ephemeral Apps、Volumes 等，不止 GPU 跑图），billed_cost =
积分抵扣后实际计费（> 0 即赠金/额度已耗尽），credits = 本期积分抵扣。
无公开「剩余积分总额」接口（plan 总额仅仪表盘会话可见）。

缓存：成功 120s、失败 20s（前端 30s 轮询，避免连续拉起子进程）；
超时 45s 强杀；任何异常转 ok=false + error 展示。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import UTC, datetime

from ..modal_volume.client import auth_env

_TTL_OK = 120.0
_TTL_FAIL = 20.0
_CLI_TIMEOUT = 45.0


def current_cycle() -> str:
    """当前 UTC 计费月（YYYY-MM），与 CLI --for / SDK cycle 语义对齐。"""
    return datetime.now(UTC).strftime("%Y-%m")


def parse_output(raw: bytes) -> dict:
    """CLI stdout（--json）→ 账单 dict；解析异常归为 ok=false（不抛出）。"""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace").strip() or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"账单 CLI 输出解析失败: {exc}"[:300]}
    if not isinstance(data, dict) or "metered_cost" not in data:
        err = data.get("error") if isinstance(data, dict) else None
        return {"ok": False, "error": str(err or "账单 CLI 返回异常数据")[:300]}

    cycle = current_cycle()

    def f(text) -> float | None:
        try:
            return round(float(text), 4)
        except (TypeError, ValueError):
            return None

    adjustments = data.get("adjustments") or {}
    breakdown = {}
    for key, value in (data.get("metered_cost_breakdown") or {}).items():
        num = f(value)
        if num:
            breakdown[key] = num
    return {
        "ok": True,
        "cycle": cycle,
        "period_start": cycle + "-01",
        "metered_cost_usd": f(data.get("metered_cost")),
        "billed_cost_usd": f(data.get("billed_cost")),
        "credits_applied_usd": abs(f(adjustments.get("credits")) or 0.0),
        "plan_cost_usd": f(adjustments.get("plan_cost")) or 0.0,
        "breakdown": dict(sorted(breakdown.items(), key=lambda kv: -(kv[1] or 0.0))),
    }


_cache: dict = {"at": -1e9, "data": None}


def _cache_fresh(now: float) -> bool:
    ttl = _TTL_OK if (_cache["data"] or {}).get("ok") else _TTL_FAIL
    return now - _cache["at"] < ttl


async def fetch(force: bool = False) -> dict:
    """本月 Modal 账单（CLI 子进程，带缓存；永不抛出，失败转 ok=false + error）。"""
    now = time.monotonic()
    if not force and _cache["data"] is not None and _cache_fresh(now):
        return _cache["data"]
    args = [shutil.which("modal") or "modal", "billing", "summary", "--for", current_cycle(), "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONWARNINGS": "ignore", **auth_env()},
        )
    except FileNotFoundError:
        data = {"ok": False, "error": "找不到 modal CLI（请安装：pip install modal，并确认在 PATH 中）"}
    except OSError as exc:
        data = {"ok": False, "error": f"无法启动账单 CLI 子进程: {exc}"[:300]}
    else:
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_CLI_TIMEOUT)
        except TimeoutError:
            proc.kill()
            data = {"ok": False, "error": f"Modal 账单查询超时（>{_CLI_TIMEOUT:g}s）"}
        else:
            if proc.returncode == 0:
                data = parse_output(out)
            else:
                msg = (err or out).decode("utf-8", errors="replace").strip()
                msg = "\n".join(  # 剥掉 CLI 的 DeprecationWarning 噪音行，保留真实错误
                    line for line in msg.splitlines()
                    if "DeprecationWarning" not in line and "set_event_loop_policy" not in line
                ).strip()
                data = {"ok": False, "error": (msg or f"modal billing 退出码 {proc.returncode}")[:300]}
    _cache.update(at=now, data=data)
    return data

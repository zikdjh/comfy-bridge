"""P-06 审计日志写入延迟基准：并发管理写操作下主链路 p95 不受审计写入拖累。

方法：TestClient 并发发 200 个写请求（audit 开启 + 写日志落盘），统计
响应 p50/p95；对照组 audit 关闭。p95 差值 < 20ms 视为「不拖累」。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import audit
from app.core.management_auth import install_management_auth, management_token


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/config")
    async def _write() -> dict:
        return {"ok": True}

    install_management_auth(app)
    return app


def _latencies(client: TestClient, n: int) -> list[float]:
    def one(_: int) -> float:
        import time

        t0 = time.perf_counter()
        r = client.post(
            "/api/v1/config",
            headers={"Authorization": f"Bearer {management_token()}"},
        )
        assert r.status_code == 200
        return (time.perf_counter() - t0) * 1000

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, range(n)))


def _pct(values: list[float], p: float) -> float:
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round(p * (len(values) - 1))))
    return values[idx]


@pytest.mark.skipif(os.name != "nt", reason="基准在 Windows 开发机执行")
def test_audit_write_does_not_block_main_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CB_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setenv("CB_AUDIT_LOG", "1")
    monkeypatch.setenv("CB_MANAGEMENT_TOKEN", "perf-token-123")
    monkeypatch.setattr(audit, "audit_enabled", lambda: True)

    app = _app()
    with TestClient(app) as client:
        _latencies(client, 30)  # 热身
        on = _latencies(client, 200)
    monkeypatch.setenv("CB_AUDIT_LOG", "0")
    monkeypatch.setattr(audit, "audit_enabled", lambda: False)

    app2 = _app()
    with TestClient(app2) as client2:
        _latencies(client2, 30)
        off = _latencies(client2, 200)

    p95_on, p95_off = _pct(on, 0.95), _pct(off, 0.95)
    delta = p95_on - p95_off
    print(f"P-06 audit on  p50/p95 = {_pct(on, 0.5):.2f}/{p95_on:.2f} ms")
    print(f"P-06 audit off p50/p95 = {_pct(off, 0.5):.2f}/{p95_off:.2f} ms")
    print(f"P-06 delta p95 = {delta:.2f} ms")
    assert delta < 20.0, f"审计写入拖累主链路 p95 {delta:.2f}ms（阈值 20ms）"

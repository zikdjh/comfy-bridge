"""P-08 云端通道 /prompt 提交延迟基准：受理路径 p95 < 500ms。

口径：云端执行为异步接力（dispatch 排队即返回，冷启动/GPU 不阻塞提交方），
故「提交延迟」= 桥接器受理路径（分类→规则→预检→落库→派发应答）。
executor.dispatch 打桩为立即成功，云端真实往返由 live smoke 覆盖。
"""

from __future__ import annotations

import json
import statistics
import time
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.config import AppConfig, BridgeConfig
from app.proxy import privacy, prompt_submit


class _Decision:
    backend = "modal"
    final_backend = "modal"
    gen_type = "image"
    trace: ClassVar[list[dict]] = []
    fallback = None


def _client(monkeypatch, stages: list[dict[str, float]]) -> TestClient:
    app = FastAPI()
    async def _prompt(request: Request):
        return await prompt_submit._handle_prompt_submit(request, timing_sink=stages.append)

    app.post("/prompt")(_prompt)
    settings = AppConfig(bridge=BridgeConfig(privacy_confirm=True))
    monkeypatch.setattr(prompt_submit, "get_settings", lambda: settings)
    monkeypatch.setattr(privacy, "get_settings", lambda: settings)
    monkeypatch.setattr(prompt_submit, "decide", lambda *a, **k: _Decision())

    async def _noop():
        return None

    async def _names():
        return set()

    async def _budget():
        return {"exceeded": False}

    async def _zero():
        return 0

    async def _inserted(*a, **k):
        return 1

    async def _snapshot_ok(*a, **k):
        return "graph.json", "hash"

    monkeypatch.setattr(prompt_submit.runner_client, "ensure_state", _noop)
    monkeypatch.setattr(prompt_submit.local_index, "local_names", _names)
    monkeypatch.setattr(prompt_submit.node_presence, "kick_refresh_if_stale", lambda: None)
    monkeypatch.setattr(prompt_submit.cloud_index, "kick_refresh_if_stale", lambda: None)
    monkeypatch.setattr(prompt_submit.cloud_index, "snapshot", dict)
    monkeypatch.setattr(prompt_submit.node_presence, "snapshot", lambda: {"local": set(), "cloud": set()})
    monkeypatch.setattr(prompt_submit.cost_svc, "abudget_state", _budget)
    monkeypatch.setattr(prompt_submit.store, "acount_active_modal", _zero)
    monkeypatch.setattr(prompt_submit.store, "asave_graph_snapshot", _snapshot_ok)
    monkeypatch.setattr(prompt_submit.store, "ainsert_request", _inserted)
    monkeypatch.setattr(prompt_submit.executor, "dispatch", lambda *a, **k: True)
    return TestClient(app)


def _pct(values: list[float], p: float) -> float:
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round(p * (len(values) - 1))))
    return values[idx]


def test_cloud_submit_p95_under_500ms(monkeypatch) -> None:
    stages: list[dict[str, float]] = []
    client = _client(monkeypatch, stages)
    body = {"prompt": {"1": {"class_type": "KSampler", "inputs": {}}}, "client_id": "perf"}
    raw = json.dumps(body).encode()
    headers = {"content-type": "application/json", "x-bridge-privacy-confirmed": "1",
               "x-bridge-workflow-name": "p08-perf"}

    latencies: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        r = client.post("/prompt", content=raw, headers=headers)
        assert r.status_code == 200
        latencies.append((time.perf_counter() - t0) * 1000)

    p50, p95 = _pct(latencies, 0.5), _pct(latencies, 0.95)
    mean = statistics.mean(latencies)
    print(f"P-08 cloud submit n=100 p50/p95/mean = {p50:.2f}/{p95:.2f}/{mean:.2f} ms")
    assert len(stages) == len(latencies)
    expected = {"read_body", "parse", "classify", "modal_state", "presence_budget",
                "decide", "preflight", "persist", "dispatch", "total"}
    assert expected <= stages[0].keys()
    stage_p95 = {
        name: _pct([sample[name] for sample in stages if name in sample], 0.95)
        for name in expected
    }
    print("P-08 stage p95", json.dumps(stage_p95, ensure_ascii=False, sort_keys=True))
    assert p95 < 500.0, f"云端受理 p95 {p95:.2f}ms 超过 500ms 目标"

"""队列合成（GET /queue）与 status 事件 queue_remaining 重写单测。"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.logs import store
from app.proxy import ws_relay


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db()
    yield
    store.close_db()


def _insert(prompt_id: str, *, status: str, backend: str = "modal",
            client_id: str | None = "browser-1", model: str = "x.safetensors") -> None:
    store.insert_request({
        "prompt_id": prompt_id,
        "created_at": store.now_iso(),
        "created_ms": store.now_ms(),
        "backend": backend,
        "final_backend": backend,
        "gen_type": "image",
        "workflow_name": None,
        "model_name": model,
        "graph_hash": "",
        "graph_file": None,
        "client_id": client_id,
        "status": status,
        "rule_trace": "[]",
        "error": None,
    })


def _queue_endpoint(local_status: int = 200, local_body: dict | None = None):
    """构造独立 /queue 端点（跳过 app.main 的真实 forward 依赖）。"""
    body = json.dumps(local_body or {"queue_running": [], "queue_pending": []}).encode()

    async def fake_forward(request):  # noqa: ANN202
        return Response(body, status_code=local_status, media_type="application/json")

    async def endpoint(request: Request) -> Response:
        local = await fake_forward(request)
        running: list[Any] = []
        pending: list[Any] = []
        if local.status_code == 200:
            with contextlib.suppress(Exception):
                data = json.loads(local.body)
                running = list(data.get("queue_running") or [])
                pending = list(data.get("queue_pending") or [])
        rows = [r for r in await store.alist_active() if r.get("final_backend") == "modal"]
        cr = [[r["prompt_id"], r["prompt_id"], None, r.get("client_id") or "", None, None, r.get("model_name") or ""]
              for r in rows if r.get("status") == "running"]
        cp = [[r["prompt_id"], r["prompt_id"], None, r.get("client_id") or "", None, None, r.get("model_name") or ""]
              for r in rows if r.get("status") == "queued"]
        return Response(json.dumps({"queue_running": running + cr,
                                     "queue_pending": pending + cp}).encode(),
                        media_type="application/json")

    return endpoint


def test_queue_merges_local_and_cloud() -> None:
    _insert("cloud-1", status="running", backend="modal")
    _insert("cloud-2", status="queued", backend="modal")
    _insert("local-1", status="running", backend="local")
    _insert("done-1", status="done", backend="modal")

    app = FastAPI()
    app.get("/queue")(_queue_endpoint(local_body={
        "queue_running": [["local-1", "local-1", None, "browser-1", None, None, "m.safetensors"]],
        "queue_pending": [],
    }))
    resp = TestClient(app).get("/queue")
    assert resp.status_code == 200
    data = resp.json()
    running_ids = [item[0] for item in data["queue_running"]]
    pending_ids = [item[0] for item in data["queue_pending"]]
    assert running_ids == ["local-1", "cloud-1"], running_ids
    assert pending_ids == ["cloud-2"], pending_ids
    assert "done-1" not in running_ids + pending_ids


def test_queue_empty_both_sides() -> None:
    app = FastAPI()
    app.get("/queue")(_queue_endpoint())
    data = TestClient(app).get("/queue").json()
    assert data == {"queue_running": [], "queue_pending": []}


def test_queue_local_upstream_error_still_returns_cloud() -> None:
    """本地队列读取失败（非 200）时不阻塞云端条目返回。"""
    _insert("cloud-1", status="running", backend="modal")
    app = FastAPI()
    app.get("/queue")(_queue_endpoint(local_status=502))
    data = TestClient(app).get("/queue").json()
    assert [i[0] for i in data["queue_running"]] == ["cloud-1"]


def test_status_rewrites_queue_remaining_with_cloud() -> None:
    _insert("cloud-1", status="running", backend="modal")
    raw = json.dumps({
        "type": "status",
        "data": {"status": {"queue_remaining": 2, "msg": ""}, "sid": "s1"},
    })
    import asyncio; rewritten = asyncio.run(ws_relay._rewrite_status_queue_remaining(raw))
    assert rewritten is not None
    event = json.loads(rewritten)
    assert event["data"]["status"]["queue_remaining"] == 3  # 2 local + 1 cloud


def test_status_no_cloud_passthrough() -> None:
    raw = json.dumps({
        "type": "status",
        "data": {"status": {"queue_remaining": 2}, "sid": "s1"},
    })
    import asyncio; assert asyncio.run(ws_relay._rewrite_status_queue_remaining(raw)) is None


def test_status_non_status_event_untouched() -> None:
    raw = json.dumps({"type": "progress", "data": {"value": 1}})
    import asyncio; assert asyncio.run(ws_relay._rewrite_status_queue_remaining(raw)) is None


def test_list_active_returns_pending_and_running() -> None:
    _insert("a", status="queued", backend="modal")
    _insert("b", status="running", backend="local")
    _insert("c", status="done", backend="modal")
    rows = store.list_active()
    ids = sorted(r["prompt_id"] for r in rows)
    assert ids == ["a", "b"]

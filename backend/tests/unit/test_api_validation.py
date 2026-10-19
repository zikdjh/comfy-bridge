"""管理 API 的请求体边界：保留路由预览的图直传兼容性，同时拒绝非法字段。"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import routing, uploads


def _routing_client(monkeypatch) -> TestClient:
    async def fake_budget() -> dict:
        return {"enabled": False, "exceeded": False}

    async def fake_active() -> int:
        return 0

    monkeypatch.setattr(routing.node_presence, "kick_refresh_if_stale", lambda: None)
    monkeypatch.setattr(
        routing.node_presence,
        "snapshot",
        lambda: {"local": {}, "cloud": {}},
    )
    monkeypatch.setattr(routing.node_presence, "last_error", lambda: None)
    monkeypatch.setattr(routing.cost_svc, "abudget_state", fake_budget)
    monkeypatch.setattr(routing.store, "acount_active_modal", fake_active)
    monkeypatch.setattr(
        routing,
        "decide",
        lambda facts, **_: SimpleNamespace(
            backend="local",
            final_backend="local",
            fallback=None,
            trace=[],
        ),
    )
    app = FastAPI()
    app.include_router(routing.router, prefix="/api/v1")
    return TestClient(app)


def test_routing_preview_accepts_direct_graph_dict(monkeypatch) -> None:
    client = _routing_client(monkeypatch)
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {"class_type": "SaveImage", "inputs": {}},
    }
    response = client.post("/api/v1/routing/preview", json=graph)
    assert response.status_code == 200
    assert response.json()["facts"]["gen_type"] == "image"


def test_routing_preview_accepts_valid_manual_backend(monkeypatch) -> None:
    client = _routing_client(monkeypatch)
    response = client.post(
        "/api/v1/routing/preview",
        json={"workflow_name": "demo.json", "manual_backend": "modal"},
    )
    assert response.status_code == 200


def test_routing_preview_rejects_invalid_manual_backend(monkeypatch) -> None:
    client = _routing_client(monkeypatch)
    for body in (
        {"manual_backend": "not-a-backend"},
        {"x-bridge-backend": "cloud"},
        {"manual_backend": 1},
    ):
        response = client.post("/api/v1/routing/preview", json=body)
        assert response.status_code == 422
        assert "local" in response.json()["detail"]


def test_upload_init_rejects_unknown_field_before_creating_session() -> None:
    app = FastAPI()
    app.include_router(uploads.router, prefix="/api/v1")
    response = TestClient(app).post(
        "/api/v1/uploads/init",
        json={
            "filename": "demo.safetensors",
            "size": 4,
            "md5": "0" * 32,
            "category": "checkpoints",
            "unexpected": True,
        },
    )
    assert response.status_code == 422
    assert "unexpected" in response.text

"""管理面鉴权边界（P0）：回环识别、令牌读取与 Basic/Bearer 中间件。"""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import management_auth as auth


def test_loopback_host_detection() -> None:
    for host in ("127.0.0.1", "127.0.0.42", "::1", "[::1]", "localhost", "LOCALHOST"):
        assert auth.is_loopback_host(host), host
    for host in ("0.0.0.0", "192.168.1.20", "example.com", "::"):
        assert not auth.is_loopback_host(host), host


def test_management_token_prefers_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(auth, "config_dir", lambda: tmp_path)
    (tmp_path / "management_token").write_text("file-token\n", encoding="utf-8")
    monkeypatch.delenv("CB_MANAGEMENT_TOKEN", raising=False)
    assert auth.management_token() == "file-token"

    monkeypatch.setenv("CB_MANAGEMENT_TOKEN", " env-token ")
    assert auth.management_token() == "env-token"


def _client(token: str = "secret-token") -> TestClient:
    app = FastAPI()
    auth.install_management_auth(app, token_getter=lambda: token)

    @app.get("/api/v1/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/bridge/")
    def bridge() -> dict:
        return {"ok": True}

    @app.get("/object_info")
    def data_plane() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_middleware_rejects_missing_and_accepts_bearer() -> None:
    client = _client()
    denied = client.get("/api/v1/ping")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Basic ")
    assert client.get("/api/v1/ping", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_middleware_accepts_basic_and_health_anonymous() -> None:
    client = _client()
    raw = base64.b64encode(b"comfybridge:secret-token").decode()
    assert client.get("/api/v1/ping", headers={"Authorization": f"Basic {raw}"}).status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/bridge/").status_code == 401


def test_data_plane_not_intercepted() -> None:
    client = _client()
    assert client.get("/object_info").status_code == 200


def test_empty_token_keeps_local_compatibility() -> None:
    client = _client(token="")
    assert client.get("/api/v1/ping").status_code == 200
    assert client.get("/bridge/").status_code == 200

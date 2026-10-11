"""管理面鉴权边界（P0/P2-1）：回环识别、令牌读取、Basic/Bearer、Origin 校验与审计。"""

from __future__ import annotations

import base64
import json

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


# ---------------------------------------------------------------- Origin 校验（CSRF）


def _write_client(token: str = "secret-token", *, extra_headers: dict | None = None) -> TestClient:
    """带写端点的管理面：用于 Origin / 审计断言。"""
    app = FastAPI()
    auth.install_management_auth(app, token_getter=lambda: token)

    @app.post("/api/v1/ping")
    def ping() -> dict:
        return {"ok": True}

    return TestClient(app, headers=extra_headers or {})


def test_same_origin_request_allowed() -> None:
    client = _write_client()
    headers = {
        "Authorization": "Bearer secret-token",
        "Origin": "http://testserver",
        "Host": "testserver",
    }
    assert client.post("/api/v1/ping", headers=headers).status_code == 200


def test_cross_origin_request_rejected() -> None:
    client = _write_client()
    headers = {
        "Authorization": "Bearer secret-token",
        "Origin": "http://evil.example",
        "Host": "testserver",
    }
    denied = client.post("/api/v1/ping", headers=headers)
    assert denied.status_code == 403
    assert "CB_ALLOWED_ORIGINS" in denied.json()["detail"]


def test_allowed_origin_env_whitelist(monkeypatch) -> None:
    monkeypatch.setenv("CB_ALLOWED_ORIGINS", "https://console.example.com, https://alt.example")
    client = _write_client()
    headers = {
        "Authorization": "Bearer secret-token",
        "Origin": "https://console.example.com",
        "Host": "testserver",
    }
    assert client.post("/api/v1/ping", headers=headers).status_code == 200
    # 白名单外的来源仍拒绝
    other = dict(headers, Origin="https://nope.example")
    assert client.post("/api/v1/ping", headers=other).status_code == 403


def test_no_origin_still_requires_token() -> None:
    client = _write_client()
    # 无 Origin 的脚本请求不被 Origin 规则拦，但仍受 token 约束
    assert client.post("/api/v1/ping").status_code == 401
    assert client.post(
        "/api/v1/ping", headers={"Authorization": "Bearer secret-token"}
    ).status_code == 200


def test_origin_check_skipped_when_no_token() -> None:
    client = _write_client(token="")
    assert client.post("/api/v1/ping", headers={"Origin": "http://evil.example"}).status_code == 200


# ---------------------------------------------------------------- 审计日志


def _audit_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_audit_records_rejections_and_writes(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "audit.log"
    monkeypatch.setenv("CB_AUDIT_LOG", "1")
    monkeypatch.setenv("CB_AUDIT_LOG_PATH", str(audit_path))
    client = _write_client()

    assert client.post("/api/v1/ping", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/v1/ping").status_code == 401
    assert client.post(
        "/api/v1/ping", headers={"Authorization": "Bearer secret-token"}
    ).status_code == 200

    rows = _audit_lines(audit_path)
    reasons = [r["reason"] for r in rows]
    assert reasons == ["cross_origin", "unauthorized", "ok"]
    assert rows[0]["status"] == 403
    assert rows[1]["status"] == 401
    assert rows[2]["auth"] == "bearer"
    # 无论如何都不落 Authorization / 凭证原文
    assert "secret-token" not in audit_path.read_text(encoding="utf-8")


def test_audit_skips_successful_reads(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "audit.log"
    monkeypatch.setenv("CB_AUDIT_LOG", "1")
    monkeypatch.setenv("CB_AUDIT_LOG_PATH", str(audit_path))
    client = _client()  # 该 app 上有成功的 GET /api/v1/ping

    assert client.get(
        "/api/v1/ping", headers={"Authorization": "Bearer secret-token"}
    ).status_code == 200
    # 只有成功的只读请求，不产生审计噪音
    assert not audit_path.exists() or _audit_lines(audit_path) == []
    # 但被拒的读请求要记
    assert client.get("/api/v1/ping", headers={"Origin": "http://evil.example"}).status_code == 403
    rows = _audit_lines(audit_path)
    assert len(rows) == 1 and rows[0]["reason"] == "cross_origin"


def test_audit_disabled_by_default_without_token(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "audit.log"
    monkeypatch.delenv("CB_AUDIT_LOG", raising=False)
    monkeypatch.setenv("CB_AUDIT_LOG_PATH", str(audit_path))
    assert auth.audit.audit_enabled() is False
    client = _client(token="")  # 未配置令牌 = 本机模式，不记审计
    assert client.get("/api/v1/ping").status_code == 200
    assert not audit_path.exists()

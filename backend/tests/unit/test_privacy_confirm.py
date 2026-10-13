"""提交前隐私确认（P2-5）：开关、确认头与 /prompt 拦截行为。"""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.core.config import AppConfig, BridgeConfig
from app.proxy import privacy, prompt_submit


class _Settings:
    class Bridge:
        privacy_confirm = True

    bridge = Bridge()


class _OffSettings:
    class Bridge:
        privacy_confirm = False

    bridge = Bridge()


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "method": "POST", "path": "/prompt"})


def test_challenge_requires_confirmation(monkeypatch):
    monkeypatch.setattr(privacy, "get_settings", lambda: _Settings())
    body = privacy.challenge(_request())
    assert body is not None
    assert body["error"] == "privacy_confirmation_required"
    assert "workflow_graph" in body["scope"]
    assert body["confirm_header"] == privacy.CONFIRM_HEADER


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_challenge_accepts_confirmation_header(monkeypatch, value):
    monkeypatch.setattr(privacy, "get_settings", lambda: _Settings())
    assert privacy.challenge(_request({privacy.CONFIRM_HEADER: value})) is None


@pytest.mark.parametrize("value", ["0", "no", "false", ""])
def test_challenge_rejects_non_true_values(monkeypatch, value):
    monkeypatch.setattr(privacy, "get_settings", lambda: _Settings())
    assert privacy.challenge(_request({privacy.CONFIRM_HEADER: value})) is not None


def test_challenge_disabled_by_switch(monkeypatch):
    monkeypatch.setattr(privacy, "get_settings", lambda: _OffSettings())
    assert privacy.challenge(_request()) is None


def test_data_scope_names_all_outbound_data():
    for word in ("工作流图快照", "输入文件", "请求元数据", "执行日志"):
        assert word in privacy.DATA_SCOPE


def test_inject_script_targets_fetch_and_confirmation_header():
    script = privacy.INJECT_SCRIPT
    assert "window.fetch" in script
    assert privacy.CONFIRM_HEADER.upper() in script.upper()
    assert "privacy_confirmation_required" in script
    assert "</script>" in script


class _Decision:
    backend = "modal"
    final_backend = "modal"
    gen_type = "image"
    trace: ClassVar[list[dict]] = []
    fallback = None


def _client(monkeypatch, *, privacy_on: bool) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.post("/prompt")(prompt_submit.handle_prompt_submit)
    settings = AppConfig(bridge=BridgeConfig(privacy_confirm=privacy_on))
    monkeypatch.setattr(prompt_submit, "get_settings", lambda: settings)
    monkeypatch.setattr(privacy, "get_settings", lambda: settings)
    monkeypatch.setattr(prompt_submit, "decide", lambda *a, **k: _Decision())
    monkeypatch.setattr(prompt_submit.runner_client, "ensure_state", lambda: _noop())
    monkeypatch.setattr(prompt_submit.local_index, "local_names", lambda: _names())
    monkeypatch.setattr(prompt_submit.node_presence, "kick_refresh_if_stale", lambda: None)
    monkeypatch.setattr(prompt_submit.cloud_index, "kick_refresh_if_stale", lambda: None)
    monkeypatch.setattr(prompt_submit.cloud_index, "snapshot", dict)
    monkeypatch.setattr(prompt_submit.node_presence, "snapshot", lambda: {"local": set(), "cloud": set()})
    monkeypatch.setattr(prompt_submit.cost_svc, "abudget_state", lambda: _budget())
    monkeypatch.setattr(prompt_submit.store, "acount_active_modal", lambda: _zero())
    monkeypatch.setattr(prompt_submit.store, "asave_graph_snapshot", lambda *a, **k: _snapshot())
    monkeypatch.setattr(prompt_submit.store, "ainsert_request", lambda *a, **k: _inserted())
    monkeypatch.setattr(prompt_submit.executor, "dispatch", lambda *a, **k: True)
    return TestClient(app)


async def _noop():
    return None


async def _names():
    return set()


async def _budget():
    return {"exceeded": False}


async def _zero():
    return 0


async def _snapshot():
    return "graph.json", "hash"


async def _inserted():
    return 1


def test_prompt_submit_returns_privacy_challenge(monkeypatch):
    client = _client(monkeypatch, privacy_on=True)
    response = client.post("/prompt", json={"prompt": {"1": {"class_type": "KSampler", "inputs": {}}}})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "privacy_confirmation_required"
    assert body["confirm_header"] == privacy.CONFIRM_HEADER


def test_prompt_submit_privacy_confirmation_header_passes_gate(monkeypatch):
    client = _client(monkeypatch, privacy_on=True)
    # 已确认后进入执行分支：这里 dispatch 未打桩会触发真实执行器；用 Header 只验证不再返回 409。
    response = client.post(
        "/prompt",
        json={"prompt": {"1": {"class_type": "KSampler", "inputs": {}}}},
        headers={privacy.CONFIRM_HEADER: "1"},
    )
    assert response.status_code != 409


def test_prompt_submit_privacy_switch_off(monkeypatch):
    client = _client(monkeypatch, privacy_on=False)
    response = client.post("/prompt", json={"prompt": {"1": {"class_type": "KSampler", "inputs": {}}}})
    assert response.status_code != 409

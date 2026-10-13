"""透传请求头改写：Origin/Referer 必须指向上游，否则 ComfyUI origin_only 403。"""

from __future__ import annotations

from fastapi import Request

from app.core.config import AppConfig, LocalConfig
from app.proxy.passthrough import upstream_headers


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "POST", "path": "/prompt"})


def test_origin_rewritten_to_upstream(monkeypatch) -> None:
    settings = AppConfig(local=LocalConfig(comfy_url="http://127.0.0.1:8188"))
    monkeypatch.setattr("app.proxy.passthrough.get_settings", lambda: settings)
    headers = upstream_headers(_request({
        "Host": "127.0.0.1:8189",
        "Origin": "http://127.0.0.1:8189",
        "Referer": "http://127.0.0.1:8189/",
        "Content-Type": "application/json",
    }))
    assert ("origin", "http://127.0.0.1:8188") in headers
    assert ("referer", "http://127.0.0.1:8188/") in headers
    assert ("host", "127.0.0.1:8189") not in headers
    assert ("content-type", "application/json") in headers


def test_hop_by_hop_stripped(monkeypatch) -> None:
    settings = AppConfig(local=LocalConfig(comfy_url="http://127.0.0.1:8188"))
    monkeypatch.setattr("app.proxy.passthrough.get_settings", lambda: settings)
    headers = upstream_headers(_request({
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=5",
        "Transfer-Encoding": "chunked",
    }))
    names = {k for k, _ in headers}
    assert not names & {"connection", "keep-alive", "transfer-encoding"}

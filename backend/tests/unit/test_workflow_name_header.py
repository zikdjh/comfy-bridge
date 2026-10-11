"""x-bridge-workflow-name 头兜底取名：API 直提交无 extra_data 时记录可读的工作流名。"""

from __future__ import annotations

from fastapi import Request

from app.proxy.prompt_submit import _extract_workflow_name, _workflow_name_fallback


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "method": "POST", "path": "/prompt"})


def test_header_fallback_returns_name() -> None:
    assert _workflow_name_fallback(_request({"X-Bridge-Workflow-Name": "  neon-city "})) == "neon-city"


def test_header_absent_or_blank_returns_none() -> None:
    assert _workflow_name_fallback(_request()) is None
    assert _workflow_name_fallback(_request({"X-Bridge-Workflow-Name": "   "})) is None


def test_header_truncated_to_200_chars() -> None:
    assert len(_workflow_name_fallback(_request({"X-Bridge-Workflow-Name": "x" * 500}))) == 200


def test_extract_prefers_extra_data_over_header() -> None:
    payload = {"extra_data": {"workflow_name": "canvas-name"}}
    assert _extract_workflow_name(payload) == "canvas-name"

"""更新检查：版本比较语义与静默降级路径。"""

from __future__ import annotations

import json

import pytest

from app.core import update_check


def test_is_newer_semver_with_beta():
    assert update_check.is_newer("v0.1.0-beta.5", "v0.1.0-beta.4")
    assert update_check.is_newer("v0.1.0-beta.4", "v0.1.0-beta.3")
    assert update_check.is_newer("v0.2.0", "v0.1.0-beta.9")
    assert not update_check.is_newer("v0.1.0-beta.3", "v0.1.0-beta.4")
    assert not update_check.is_newer("v0.1.0-beta.4", "v0.1.0-beta.4")
    # 正式版 > 任何 pre-release
    assert update_check.is_newer("v0.1.0", "v0.1.0-beta.9")


def test_is_newer_garbage_is_false():
    assert not update_check.is_newer("not-a-version", "v0.1.0-beta.4")
    assert not update_check.is_newer("v0.1.0-beta.5", "garbage")


@pytest.mark.asyncio
async def test_check_update_not_packaged(monkeypatch, tmp_path):
    monkeypatch.setattr(update_check, "read_current_version", lambda: (None, None))
    result = await update_check.check_update()
    assert result["available"] is False
    assert result["reason"] == "not_packaged"


@pytest.mark.asyncio
async def test_check_update_cache_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        update_check,
        "read_current_version",
        lambda: ("v0.1.0-beta.4", tmp_path / "version.txt"),
    )
    cache = tmp_path / "update_check.json"
    monkeypatch.setattr(update_check, "_CACHE_PATH", cache)
    cache.write_text(json.dumps({
        "available": True, "current": "v0.1.0-beta.4", "latest": "v0.1.0-beta.5",
        "release_url": "https://example.com", "reason": "ok",
        "checked_at": __import__("time").time(),
    }), encoding="utf-8")
    result = await update_check.check_update()
    assert result["available"] is True
    assert result["cached"] is True


@pytest.mark.asyncio
async def test_check_update_network_failure_degrades(monkeypatch, tmp_path):
    monkeypatch.setattr(
        update_check,
        "read_current_version",
        lambda: ("v0.1.0-beta.4", tmp_path / "version.txt"),
    )
    monkeypatch.setattr(update_check, "_CACHE_PATH", tmp_path / "update_check.json")

    class _Boom:
        async def __aenter__(self):
            raise OSError("network down")

        async def __aexit__(self, *a):
            return False

    class _FakeHttpx:
        def AsyncClient(self, **kwargs):
            return _Boom()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _FakeHttpx().AsyncClient, raising=False)

    result = await update_check.check_update(force=True)
    assert result["available"] is False
    assert result["reason"] == "check_failed"

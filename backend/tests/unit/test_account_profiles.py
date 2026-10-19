"""多账号档案 API 单测（docs/ComfyBridge-设计-多账号维护.html）。

get_settings / CONFIG_PATH / count_active_modal 一律 patch——不读真实
~/.comfybridge/config.toml，不碰真实任务库；toml 写回用临时目录真跑。
"""

import asyncio
import tomllib

import pytest
from fastapi import HTTPException

from app.api.v1 import config as config_api
from app.core.config import AccountProfile, AppConfig, BridgeConfig, LocalConfig, ModalConfig


def _modal(**kwargs) -> ModalConfig:
    base = {"token_id": "ak-cur", "token_secret": "as-cur-secret", "proxy_token_id": "wk-cur", "proxy_token_secret": "ws-cur-secret"}
    base.update(kwargs)
    return ModalConfig(**base)


def _install(monkeypatch, tmp_path, initial_modal, active_tasks=0):
    box = {"modal": initial_modal}

    def fake_get_settings():
        return AppConfig(local=LocalConfig(), bridge=BridgeConfig(), modal=box["modal"])

    fake_get_settings.cache_clear = lambda: None
    monkeypatch.setattr(config_api, "get_settings", fake_get_settings)
    monkeypatch.setattr(config_api, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_api, "count_active_modal", lambda: active_tasks)
    events: list[dict] = []
    monkeypatch.setattr(config_api.audit, "record", lambda event: events.append(event))

    real_persist = config_api._persist_settings

    def persist(local, bridge, modal):
        real_persist(local, bridge, modal)  # 真写临时 toml（cache_clear 为 noop）
        box["modal"] = modal  # 同步测试态，模拟下次 get_settings 读到新配置

    monkeypatch.setattr(config_api, "_persist_settings", persist)
    invalidated: list[str] = []
    monkeypatch.setattr(config_api, "_invalidate_account_caches", lambda: invalidated.append("x"))
    return box, events, invalidated


def _upsert(**kwargs) -> dict:
    return asyncio.run(config_api.upsert_account(config_api.AccountUpsertBody(**kwargs)))


def _activate(name: str, force: bool = False) -> dict:
    return asyncio.run(config_api.activate_account(name, config_api.AccountActivateBody(force=force)))


# ---------------------------------------------------------------- 档案 CRUD

def test_upsert_new_profile_requires_secret(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal())
    with pytest.raises(HTTPException) as exc:
        _upsert(name="team", token_id="ak-team")
    assert exc.value.status_code == 422


def test_upsert_new_profile_reuses_current_secret(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal())
    out = _upsert(name="current-copy", token_id="ak-cur", volume="comfyui-data")
    assert out["active"] == "current-copy"  # token 与当前一致 → 立即为活跃档案
    assert out["accounts"][0]["has_secret"] is True


def test_same_token_snapshot_can_be_deleted_when_explicit_active(monkeypatch, tmp_path):
    modal = _modal(
        accounts=[
            AccountProfile(name="current", token_id="ak-cur", token_secret="as-c"),
            AccountProfile(name="snapshot", token_id="ak-cur", token_secret="as-c"),
        ],
        active_account="current",
    )
    box, _events, _invalidated = _install(monkeypatch, tmp_path, modal)
    out = asyncio.run(config_api.delete_account("snapshot"))
    assert [a["name"] for a in out["accounts"]] == ["current"]
    assert out["active"] == "current"
    assert box["modal"].active_account == "current"


def test_upsert_rejects_duplicate_token_id(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(accounts=[AccountProfile(name="a", token_id="ak-1")]))
    with pytest.raises(HTTPException) as exc:
        _upsert(name="b", token_id="ak-1", token_secret="as-x")
    assert exc.value.status_code == 409


def test_upsert_overwrite_keeps_secret_when_blank(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(accounts=[
        AccountProfile(name="team", token_id="ak-team", token_secret="as-keep", volume="old-vol"),
    ]))
    out = _upsert(name="team", token_id="ak-team", volume="new-vol", web_url="https://team.modal.run")
    entry = next(a for a in out["accounts"] if a["name"] == "team")
    assert entry["volume"] == "new-vol"
    assert entry["has_secret"] is True and "keep" not in entry["secret_masked"]


def test_upsert_rejects_empty_name_or_token(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal())
    with pytest.raises(HTTPException):
        _upsert(name="  ", token_id="ak-1", token_secret="as-x")
    with pytest.raises(HTTPException):
        _upsert(name="x", token_id="", token_secret="as-x")


def test_delete_account_rules(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(accounts=[
        AccountProfile(name="other", token_id="ak-other", token_secret="as-o"),
        AccountProfile(name="current", token_id="ak-cur", token_secret="as-c"),
    ]))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(config_api.delete_account("nope"))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        asyncio.run(config_api.delete_account("current"))  # token_id == ak-cur → 活跃档案
    assert exc.value.status_code == 409
    out = asyncio.run(config_api.delete_account("other"))
    assert [a["name"] for a in out["accounts"]] == ["current"]


# ---------------------------------------------------------------- 激活切换

def test_activate_copies_fields_and_invalidates(monkeypatch, tmp_path):
    box, events, invalidated = _install(monkeypatch, tmp_path, _modal(accounts=[
        AccountProfile(
            name="team", token_id="ak-team", token_secret="as-team-secret-0001",
            environment="prod", proxy_token_id="wk-team", proxy_token_secret="ws-team",
            volume="team-vol", app_name="team-app", web_url="https://team.modal.run",
            task_timeout=1800, extra_launch_args=["--fast"],
        ),
    ]))
    out = _activate("team")
    assert invalidated == ["x"]
    assert out["active"] == "team"
    assert out["modal"]["token_id"] == "ak-team"
    assert out["modal"]["secret_masked"].startswith("as-te****")
    assert out["modal"]["volume"] == "team-vol"
    assert out["modal"]["app_name"] == "team-app"
    assert out["modal"]["web_url"] == "https://team.modal.run"
    assert out["modal"]["extra_launch_args"] == ["--fast"]
    assert box["modal"].token_id == "ak-team"
    assert any(e["event"] == "account_activate" for e in events)


def test_activate_guard_blocks_with_active_tasks(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(accounts=[
        AccountProfile(name="team", token_id="ak-team", token_secret="as-team"),
    ]), active_tasks=2)
    with pytest.raises(HTTPException) as exc:
        _activate("team")
    assert exc.value.status_code == 409
    out = _activate("team", force=True)
    assert out["active"] == "team"


def test_activate_idempotent_when_already_active(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(accounts=[
        AccountProfile(name="current", token_id="ak-cur", token_secret="as-c"),
    ]))
    out = _activate("current")  # ak-cur → 活跃档案
    assert out["active"] == "current"


def test_activate_unknown_profile_404(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal())
    with pytest.raises(HTTPException) as exc:
        _activate("nope")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- 脱敏与序列化

def test_accounts_never_leak_plaintext_secrets(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(accounts=[
        AccountProfile(name="team", token_id="ak-team", token_secret="as-team-secret-long", proxy_token_secret="ws-team-secret-long"),
    ]))
    out = asyncio.run(config_api.list_accounts())
    blob = str(out)
    assert "as-team-secret-long" not in blob and "ws-team-secret-long" not in blob
    assert "****" in blob


def test_upsert_short_secret_fully_masked(monkeypatch, tmp_path):
    """超短 secret（API-07 观察的长度边界）必须全掩码，任何响应路径不得回显原文。"""
    _install(monkeypatch, tmp_path, _modal())
    out = _upsert(name="tiny", token_id="ak-tiny", token_secret="7chars!")
    entry = next(a for a in out["accounts"] if a["name"] == "tiny")
    assert entry["has_secret"] is True
    assert entry["secret_masked"] == "****"
    assert "7chars!" not in str(out)


def test_config_view_masks_short_modal_secret(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _modal(token_secret="tiny7", proxy_token_secret="ws-tiny7"))
    view = config_api._view()
    assert view["modal"]["secret_masked"] == "****"
    assert view["modal"]["proxy_secret_masked"] == "****"
    assert "tiny7" not in str(view)


def test_accounts_toml_roundtrip(tmp_path):
    modal = _modal(accounts=[
        AccountProfile(name="team", token_id="ak-team", token_secret="as-team", web_url="https://team.modal.run"),
    ])
    text = config_api._dump_toml({
        "local": LocalConfig().model_dump(),
        "bridge": BridgeConfig().model_dump(),
        "modal": modal.model_dump(),
    })
    data = tomllib.loads(text)
    assert data["modal"]["accounts"][0]["token_id"] == "ak-team"
    parsed = AppConfig(modal=data["modal"])
    assert parsed.modal.accounts[0].name == "team"
    assert parsed.modal.accounts[0].token_secret == "as-team"


def test_legacy_toml_without_accounts_loads(tmp_path):
    text = config_api._dump_toml({
        "local": LocalConfig().model_dump(),
        "bridge": BridgeConfig().model_dump(),
        "modal": _modal().model_dump(exclude={"accounts"}),
    })
    data = tomllib.loads(text)
    parsed = AppConfig(modal=data["modal"])
    assert parsed.modal.accounts == []
    assert parsed.modal.token_id == "ak-cur"


# ---------------------------------------------------------------- 缓存失效钩子

def test_volume_usage_invalidate_bumps_generation(monkeypatch):
    from app.volume_mgr import usage
    usage._CACHE.update(computing=True, total_bytes=123, volumes=[{"name": "x"}])
    gen_before = usage._CACHE["generation"]
    usage.invalidate_cache()
    assert usage._CACHE["generation"] == gen_before + 1
    assert usage._CACHE["computing"] is False
    assert usage._CACHE["total_bytes"] == 0 and usage._CACHE["volumes"] == []


async def test_usage_compute_resets_flag_when_generation_changes(monkeypatch):
    """在途统计因账号切换作废时也必须复位 computing。"""
    from app.volume_mgr import usage

    usage.invalidate_cache()
    before = usage._CACHE["generation"]
    usage._CACHE["computing"] = True

    async def fake_volumes_list():
        usage._CACHE["generation"] = before + 1
        return []

    monkeypatch.setattr(usage.modal_cli, "volumes_list", fake_volumes_list)
    await usage._compute()
    assert usage._CACHE["computing"] is False


async def test_usage_compute_surfaces_rate_limit_and_preserves_snapshot(monkeypatch):
    from app.modal_volume.client import ModalCliError
    from app.volume_mgr import usage

    usage._CACHE.update(
        computing=True,
        computed_ms=123,
        volumes=[{"name": "cached", "total_bytes": 9, "file_count": 1}],
        total_bytes=9,
        file_count=1,
        error=None,
    )

    async def fake_volumes_list():
        raise ModalCliError("VolumeListFiles rate limit exceeded", category="rate_limited")

    monkeypatch.setattr(usage.modal_cli, "volumes_list", fake_volumes_list)
    await usage._compute()
    assert usage._CACHE["computing"] is False
    assert usage._CACHE["volumes"][0]["name"] == "cached"
    assert "rate limit" in (usage._CACHE["error"] or "")


async def test_usage_compute_surfaces_nested_rate_limit(monkeypatch):
    from app.modal_volume.client import ModalCliError
    from app.volume_mgr import usage

    usage._CACHE.update(
        computing=True,
        computed_ms=123,
        volumes=[{"name": "cached", "total_bytes": 9, "file_count": 1}],
        total_bytes=9,
        file_count=1,
        error=None,
    )

    async def fake_volumes_list():
        return [{"name": "comfyui-data", "created_at": ""}]

    async def fake_list_dir(_rel, *, volume):
        assert volume == "comfyui-data"
        raise ModalCliError("VolumeListFiles rate limit exceeded", category="rate_limited")

    monkeypatch.setattr(usage.modal_cli, "volumes_list", fake_volumes_list)
    monkeypatch.setattr(usage.modal_cli, "list_dir", fake_list_dir)
    await usage._compute()
    assert usage._CACHE["computing"] is False
    assert usage._CACHE["volumes"][0]["name"] == "cached"
    assert "rate limit" in (usage._CACHE["error"] or "")


async def test_usage_compute_times_out_and_preserves_snapshot(monkeypatch):
    from app.volume_mgr import usage

    usage._CACHE.update(
        computing=True,
        computed_ms=123,
        volumes=[{"name": "cached", "total_bytes": 9, "file_count": 1}],
        total_bytes=9,
        file_count=1,
        error=None,
    )
    monkeypatch.setattr(usage, "_COMPUTE_TIMEOUT", 0.01)

    async def hanging_volumes_list():
        await asyncio.sleep(60)
        return []

    monkeypatch.setattr(usage.modal_cli, "volumes_list", hanging_volumes_list)
    await usage._compute()
    assert usage._CACHE["computing"] is False
    assert usage._CACHE["volumes"][0]["name"] == "cached"
    assert usage._CACHE["total_bytes"] == 9
    assert "超时" in (usage._CACHE["error"] or "")


def test_runner_invalidate_state():
    from app.runner import client as runner_client
    runner_client._state.update(fetched_ms=1.0, state="deployed")
    runner_client.invalidate_state()
    assert runner_client._state["state"] is None
    assert runner_client.deployed_state() is None


def test_diff_cache_invalidate_all(tmp_path, monkeypatch):
    from app.nodes_mgr import diff_cache
    monkeypatch.setattr(diff_cache, "config_dir", lambda: tmp_path)
    diff_cache._doc = {"schema": diff_cache._SCHEMA, "matrix": {"rows": [1]}}
    diff_cache._save()
    assert (tmp_path / "nodes_diff_cache.json").exists()
    diff_cache.invalidate_all()
    assert diff_cache._doc is None
    assert not (tmp_path / "nodes_diff_cache.json").exists()

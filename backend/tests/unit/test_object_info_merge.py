"""P1-4：/object_info 策略化合并、冲突诊断与缓存失效。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.v1 import config as config_api
from app.api.v1 import nodes as nodes_api
from app.core.config import AppConfig, BridgeConfig
from app.nodes_mgr import diff_cache
from app.proxy import object_info


def _settings(strategy: str = "local-first"):
    return AppConfig(bridge=BridgeConfig(object_info_merge=strategy))


def _local_doc() -> dict:
    return {
        "KSampler": {
            "input": {
                "required": {
                    "ckpt_name": [["local.safetensors"]],
                    "steps": [20],
                },
            },
            "output": ["LATENT"],
        },
        "LocalOnly": {"input": {"required": {}}, "output": []},
    }


def _cloud_doc() -> dict:
    return {
        "KSampler": {
            "input": {
                "required": {
                    "ckpt_name": [["cloud.safetensors"]],
                    "steps": [30],
                },
            },
            "output": ["LATENT"],
        },
        "CloudOnly": {
            "input": {"required": {"lora_name": [["cloud-lora.safetensors"]]}},
            "output": ["MODEL"],
        },
    }


@pytest.fixture(autouse=True)
def clean_merge_state(monkeypatch):
    object_info._cache.clear()
    object_info._cloud_schemas.clear()
    object_info._cloud_conflicts.clear()
    object_info._cloud_schema_stamp = None
    diff_cache._doc = None
    monkeypatch.setattr(
        object_info.cloud_index,
        "snapshot",
        lambda: {"checkpoints": {"cloud.safetensors"}},
    )
    monkeypatch.setattr(object_info.cloud_index, "stamp", lambda: (123.0, 1))
    yield
    object_info._cache.clear()
    object_info._cloud_schemas.clear()
    object_info._cloud_conflicts.clear()
    object_info._cloud_schema_stamp = None
    diff_cache._doc = None


def test_local_first_keeps_local_schema_and_adds_model(monkeypatch) -> None:
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("local-first"))
    object_info.set_cloud_schemas(_cloud_doc(), _local_doc())
    merged = object_info._merge_doc(_local_doc())

    assert set(merged) == {"KSampler", "LocalOnly", "CloudOnly"}
    assert merged["KSampler"]["input"]["required"]["steps"] == [20]
    assert merged["KSampler"]["input"]["required"]["ckpt_name"][0] == [
        "local.safetensors",
        "cloud.safetensors",
    ]
    # local-first 不把云端同名节点的独有 combo 值并入本地 schema
    assert merged["CloudOnly"]["input"]["required"]["lora_name"][0] == [
        "cloud-lora.safetensors"
    ]


def test_union_keeps_local_structure_and_adds_cloud_only_nodes(monkeypatch) -> None:
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("union"))
    object_info.set_cloud_schemas(_cloud_doc(), _local_doc())
    merged = object_info._merge_doc(_local_doc())

    assert set(merged) == {"KSampler", "LocalOnly", "CloudOnly"}
    # 本地结构优先：steps 仍是 20，不被云端 30 覆盖
    assert merged["KSampler"]["input"]["required"]["steps"] == [20]
    # union 把云端模型并入本地 combo
    assert merged["KSampler"]["input"]["required"]["ckpt_name"][0] == [
        "local.safetensors",
        "cloud.safetensors",
    ]
    assert merged["CloudOnly"]["input"]["required"]["lora_name"][0] == [
        "cloud-lora.safetensors"
    ]


def test_local_only_ignores_cloud_schema_and_models(monkeypatch) -> None:
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("local-only"))
    object_info.set_cloud_schemas(_cloud_doc(), _local_doc())
    merged = object_info._merge_doc(_local_doc())

    assert set(merged) == {"KSampler", "LocalOnly"}
    assert merged["KSampler"]["input"]["required"]["ckpt_name"][0] == ["local.safetensors"]


def test_set_cloud_schemas_reports_conflict_paths() -> None:
    result = object_info.set_cloud_schemas(_cloud_doc(), _local_doc())
    assert result["count"] == 2
    assert "KSampler" in result["conflicts"]
    assert "input.required.ckpt_name" in result["conflicts"]["KSampler"]
    assert "input.required.steps" in result["conflicts"]["KSampler"]
    assert not any("[0]" in path for path in result["conflicts"]["KSampler"])
    assert "CloudOnly" not in result["conflicts"]  # 仅云端独有，不算同名冲突


async def test_handle_object_info_uses_strategy_and_invalidates_cache(monkeypatch) -> None:
    current = {"strategy": "local-first"}
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings(current["strategy"]))

    class FakeClient:
        async def get(self, *args, **kwargs):
            return httpx.Response(200, json=_local_doc(), request=httpx.Request("GET", args[0]))

    monkeypatch.setattr(object_info, "get_upstream_client", lambda: FakeClient())
    object_info.set_cloud_schemas(_cloud_doc(), _local_doc())

    app = FastAPI()

    @app.get("/object_info")
    async def endpoint(request: Request):
        return await object_info.handle_object_info(request, None)

    client = TestClient(app)
    first = client.get("/object_info").json()
    assert "CloudOnly" in first
    assert first["KSampler"]["input"]["required"]["ckpt_name"][0] == [
        "local.safetensors",
        "cloud.safetensors",
    ]

    current["strategy"] = "union"
    object_info.invalidate_merge_cache()
    second = client.get("/object_info").json()
    assert "CloudOnly" in second
    # 缓存失效后确实按新策略重算，而不是继续返回旧结果
    assert second["KSampler"]["input"]["required"]["ckpt_name"][0] == [
        "local.safetensors",
        "cloud.safetensors",
    ]


async def test_handle_object_info_single_node_does_not_add_cloud_only(monkeypatch) -> None:
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("local-first"))

    class FakeClient:
        async def get(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={"KSampler": _local_doc()["KSampler"]},
                request=httpx.Request("GET", args[0]),
            )

    monkeypatch.setattr(object_info, "get_upstream_client", lambda: FakeClient())
    object_info.set_cloud_schemas(_cloud_doc(), _local_doc())

    app = FastAPI()

    @app.get("/object_info/{node_type}")
    async def endpoint(node_type: str, request: Request):
        return await object_info.handle_object_info(request, node_type)

    result = TestClient(app).get("/object_info/KSampler").json()
    assert set(result) == {"KSampler"}
    assert "CloudOnly" not in result
    assert result["KSampler"]["input"]["required"]["ckpt_name"][0] == [
        "local.safetensors",
        "cloud.safetensors",
    ]


def test_save_config_invalidates_object_info_cache(monkeypatch, tmp_path) -> None:
    object_info._cache["/object_info"] = (b"old", ("stamp",), b"old")
    monkeypatch.setattr(config_api, "CONFIG_PATH", tmp_path / "config.toml")
    class FakeSettings:
        bridge = BridgeConfig(object_info_merge="union")
        local = _settings().local
        modal = _settings().modal

    fake = FakeSettings()

    def fake_get_settings():
        return fake

    fake_get_settings.cache_clear = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(config_api, "get_settings", fake_get_settings)
    config_api.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 直接调用更新函数，验证成功写盘路径也会清空合并缓存
    config_api.update_bridge_section({"object_info_merge": "union"})
    assert object_info._cache == {}


async def test_nodes_diff_caches_conflicts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(diff_cache, "_path", lambda: tmp_path / "diff.json")
    diff_cache._doc = None
    async def fake_matrix():
        return {
            "ok": True,
            "cloud_error": None,
            "app_state": "running",
            "volume": "vol",
            "manifest": {"local": {}, "cloud": {}},
            "rows": [],
            "counts": {"synced": 0, "only_local": 0, "only_cloud": 0},
        }

    monkeypatch.setattr(nodes_api, "_compute_matrix", fake_matrix)
    async def fake_local():
        return _local_doc()

    async def fake_cloud():
        return _cloud_doc()

    monkeypatch.setattr(nodes_api, "_local_object_info", fake_local)
    monkeypatch.setattr(nodes_api.runner_client, "fetch_object_info", fake_cloud)
    monkeypatch.setattr(nodes_api, "get_settings", lambda: _settings("union"))
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("union"))

    result = await nodes_api.nodes_diff(deep=True, force=True)
    assert result["class_types"]["merge_strategy"] == "union"
    assert "KSampler" in result["class_types"]["conflicts"]
    assert diff_cache.load_class_types()["conflicts"]["KSampler"]
    assert object_info._cloud_schemas  # deep 对比同时填充 /object_info 合并源


async def test_nodes_diff_restores_cloud_schemas_from_persistent_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(diff_cache, "_path", lambda: tmp_path / "diff.json")
    diff_cache._doc = None

    async def fake_matrix():
        return {
            "ok": True,
            "cloud_error": None,
            "app_state": "running",
            "volume": "vol",
            "manifest": {"local": {}, "cloud": {}},
            "rows": [],
            "counts": {"synced": 0, "only_local": 0, "only_cloud": 0},
        }

    monkeypatch.setattr(nodes_api, "_compute_matrix", fake_matrix)

    async def fake_local():
        return _local_doc()

    calls = {"cloud": 0}

    async def fake_cloud():
        calls["cloud"] += 1
        return _cloud_doc()

    monkeypatch.setattr(nodes_api, "_local_object_info", fake_local)
    monkeypatch.setattr(nodes_api.runner_client, "fetch_object_info", fake_cloud)
    monkeypatch.setattr(nodes_api, "get_settings", lambda: _settings("union"))
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("union"))

    await nodes_api.nodes_diff(deep=True, force=True)
    assert calls["cloud"] == 1
    assert diff_cache.load_cloud_schemas()

    # 模拟进程重启：内存态清空，但磁盘上的 deep 缓存仍在。
    object_info._cloud_schemas.clear()
    object_info._cloud_conflicts.clear()
    object_info._cloud_schema_stamp = None

    result = await nodes_api.nodes_diff(deep=True, force=False)
    assert result["class_types"]["cached"] is True
    assert calls["cloud"] == 1
    assert set(object_info._cloud_schemas) == {"KSampler", "CloudOnly"}
    assert object_info._cloud_conflicts["KSampler"]


async def test_nodes_diff_failed_force_refresh_keeps_last_persisted_schemas(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(diff_cache, "_path", lambda: tmp_path / "diff.json")
    diff_cache._doc = None

    async def fake_matrix():
        return {
            "ok": True,
            "cloud_error": None,
            "app_state": "running",
            "volume": "vol",
            "manifest": {"local": {}, "cloud": {}},
            "rows": [],
            "counts": {"synced": 0, "only_local": 0, "only_cloud": 0},
        }

    monkeypatch.setattr(nodes_api, "_compute_matrix", fake_matrix)

    async def fake_local():
        return _local_doc()

    async def good_cloud():
        return _cloud_doc()

    async def bad_cloud():
        raise RuntimeError("cloud unavailable")

    monkeypatch.setattr(nodes_api, "_local_object_info", fake_local)
    monkeypatch.setattr(nodes_api, "get_settings", lambda: _settings("union"))
    monkeypatch.setattr(object_info, "get_settings", lambda: _settings("union"))
    monkeypatch.setattr(nodes_api.runner_client, "fetch_object_info", good_cloud)
    await nodes_api.nodes_diff(deep=True, force=True)
    persisted = diff_cache.load_cloud_schemas()
    assert set(persisted) == {"KSampler", "CloudOnly"}

    # 失败刷新不得把内存中的陈旧/不完整 schema 写回持久缓存。
    object_info._cloud_schemas.clear()
    object_info._cloud_schemas["StaleOnly"] = {"input": {}}
    monkeypatch.setattr(nodes_api.runner_client, "fetch_object_info", bad_cloud)
    result = await nodes_api.nodes_diff(deep=True, force=True)

    assert result["class_types"]["cloud"] is None
    assert result["class_types"]["error"] == "cloud unavailable"
    assert set(diff_cache.load_cloud_schemas()) == {"KSampler", "CloudOnly"}

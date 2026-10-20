"""云端资源规格（设置 → Modal → 云端资源规格）。

Modal 的 @app.function(gpu/cpu/memory) 只能在 deploy 时固化，因此本测试覆盖整条
链路：配置落盘与校验 → nodes_mgr.deploy 注入 CB_MODAL_* 环境变量 → runner.py 读取。
get_settings / CONFIG_PATH 一律 patch，不读也不写真实的 ~/.comfybridge。
"""

from __future__ import annotations

import asyncio
import importlib.util
import tomllib
import uuid

import pytest
from fastapi import HTTPException

from app import runner_assets
from app.api.v1 import config as config_api
from app.core.config import AppConfig, BridgeConfig, LocalConfig, ModalConfig
from app.nodes_mgr import deploy


def _install(monkeypatch, tmp_path, modal: ModalConfig | None = None) -> None:
    box = {"modal": modal or ModalConfig()}
    path = tmp_path / "config.toml"

    def fake_get_settings():
        # 写入后回读临时 toml，模拟真实 get_settings 的热生效语义
        if path.is_file():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            known = {k: v for k, v in data.items() if k in {"local", "bridge", "modal"}}
            return AppConfig(**known)
        return AppConfig(local=LocalConfig(), bridge=BridgeConfig(), modal=box["modal"])

    fake_get_settings.cache_clear = lambda: None  # write_config 内部会调 cache_clear
    monkeypatch.setattr(config_api, "get_settings", fake_get_settings)
    monkeypatch.setattr(config_api, "CONFIG_PATH", path)
    monkeypatch.setattr(config_api.object_info_merge, "invalidate_merge_cache", lambda: None)
    monkeypatch.setattr(config_api.audit, "record", lambda event: None)

    async def _noop() -> None:
        return None

    monkeypatch.setattr(config_api.passthrough, "close_upstream_client", _noop)


def _put(modal: dict) -> dict:
    return asyncio.run(config_api.write_config(config_api.ConfigBody(modal=modal)))


# ---------------------------------------------------------------- 配置与校验

def test_modal_defaults_match_runner_defaults():
    modal = ModalConfig()
    assert (modal.gpu, modal.cpu, modal.memory_mb) == ("L40S", 4.0, 32768)


def test_resources_saved_and_echoed(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    view = _put({"gpu": "H100:2", "cpu": 8, "memory_mb": 65536})
    assert view["modal"]["gpu"] == "H100:2"
    assert view["modal"]["cpu"] == 8
    assert view["modal"]["memory_mb"] == 65536
    # 真写临时 toml，落盘值与回显一致
    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert data["modal"]["gpu"] == "H100:2"
    assert data["modal"]["cpu"] == 8
    assert data["modal"]["memory_mb"] == 65536


@pytest.mark.parametrize(
    "payload",
    [
        {"gpu": ""},
        {"gpu": "H100; rm -rf /"},
        {"cpu": 0},
        {"cpu": 65},
        {"cpu": "many"},
        {"memory_mb": 128},
        {"memory_mb": 999999999},
    ],
)
def test_invalid_resources_rejected(monkeypatch, tmp_path, payload):
    _install(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc:
        _put(payload)
    assert exc.value.status_code == 422


# ---------------------------------------------------------------- 部署注入

def test_resource_env_reflects_config(monkeypatch):
    modal = ModalConfig(gpu="H100:2", cpu=2.5, memory_mb=16384)

    def fake_get_settings():
        return AppConfig(local=LocalConfig(), bridge=BridgeConfig(), modal=modal)

    monkeypatch.setattr(deploy, "get_settings", fake_get_settings)
    assert deploy.resource_env() == {
        "CB_MODAL_GPU": "H100:2",
        "CB_MODAL_CPU": "2.5",
        "CB_MODAL_MEMORY_MB": "16384",
    }


def test_apply_requires_credentials(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    monkeypatch.setattr(config_api, "modal_configured", lambda: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(config_api.apply_modal_resources())
    assert exc.value.status_code == 400


def test_apply_conflicts_with_running_deploy(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, ModalConfig(token_id="ak-x", token_secret="as-x"))
    monkeypatch.setattr(config_api, "modal_configured", lambda: True)

    async def busy(label: str) -> dict:
        raise RuntimeError("已有镜像部署任务在进行中")

    monkeypatch.setattr(config_api.deploy, "begin_exclusive", busy)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(config_api.apply_modal_resources())
    assert exc.value.status_code == 409


# ---------------------------------------------------------------- runner 解析

def _load_runner(monkeypatch, **env: str):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    pytest.importorskip("modal")
    spec = importlib.util.spec_from_file_location(
        f"cb_runner_{uuid.uuid4().hex}", runner_assets.runner_file("runner.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_reads_injected_specs(monkeypatch):
    module = _load_runner(
        monkeypatch,
        CB_MODAL_GPU="H100:2",
        CB_MODAL_CPU="2.5",
        CB_MODAL_MEMORY_MB="16384",
    )
    assert (module.GPU_SPEC, module.CPU_CORES, module.MEMORY_MB) == ("H100:2", 2.5, 16384)


def test_runner_falls_back_on_invalid_values(monkeypatch):
    module = _load_runner(
        monkeypatch, CB_MODAL_GPU="   ", CB_MODAL_CPU="0", CB_MODAL_MEMORY_MB="abc"
    )
    assert (module.GPU_SPEC, module.CPU_CORES, module.MEMORY_MB) == ("L40S", 4.0, 32768)

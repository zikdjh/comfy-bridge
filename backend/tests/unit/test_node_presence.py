"""M3-2 节点 class 两端在场索引：刷新/持久化/单端故障保留 + 规则校验器对新类型的接纳。

模块状态全部经 fixture 重置；_state_path 指向 tmp_path，绝不碰真实 ~/.comfybridge。
"""

import pytest

from app.models_mgr import node_presence as np
from app.routing import engine


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(np, "_state_path", lambda: tmp_path / "node_presence.json")
    np._state.update({"local": set(), "cloud": set(), "fetched_ms": 0.0, "loaded": False})
    np.__dict__["_error"] = None
    yield


def _patch_fetchers(monkeypatch, local=None, cloud=None, local_exc=None, cloud_exc=None):
    async def _local():
        if local_exc:
            raise local_exc
        return local or set()

    async def _cloud():
        if cloud_exc:
            raise cloud_exc
        return cloud or set()

    monkeypatch.setattr(np, "_fetch_local", _local)
    monkeypatch.setattr(np, "_fetch_cloud", _cloud)


# ---------------------------------------------------------------- 刷新与持久化

async def test_refresh_fetches_both_sides_and_persists(fresh, monkeypatch, tmp_path):
    _patch_fetchers(monkeypatch, local={"KSampler", "SaveImage"}, cloud={"VHS_VideoCombine", "KSampler"})
    snap = await np.refresh(force=True)
    assert snap["local"] == {"KSampler", "SaveImage"}
    assert snap["cloud"] == {"VHS_VideoCombine", "KSampler"}
    assert np.last_error() is None
    # 落盘内容可被"新进程"（重置内存态）懒加载恢复
    np._state.update({"local": set(), "cloud": set(), "loaded": False})
    again = np.snapshot()
    assert again["local"] == {"KSampler", "SaveImage"}
    assert again["cloud"] == {"VHS_VideoCombine", "KSampler"}


async def test_refresh_partial_failure_keeps_old_side(fresh, monkeypatch):
    np._state["local"] = {"OldLocal"}
    np._state["loaded"] = True
    _patch_fetchers(monkeypatch, local_exc=RuntimeError("本地未启动"), cloud={"CloudOnly"})
    snap = await np.refresh(force=True)
    assert snap["local"] == {"OldLocal"}  # 单端失败不清空既有集合
    assert snap["cloud"] == {"CloudOnly"}
    assert np.last_error() and "本地未启动" in np.last_error()


async def test_refresh_ttl_skips_when_fresh(fresh, monkeypatch):
    import time

    np._state.update({"local": {"A"}, "cloud": {"B"}, "fetched_ms": time.time(), "loaded": True})

    async def _boom():
        raise AssertionError("fresh cache must not trigger fetch")

    monkeypatch.setattr(np, "_fetch_local", _boom)
    monkeypatch.setattr(np, "_fetch_cloud", _boom)
    snap = await np.refresh()  # 不带 force
    assert snap == {"local": {"A"}, "cloud": {"B"}}


def test_snapshot_missing_file_is_empty(fresh):
    snap = np.snapshot()
    assert snap == {"local": set(), "cloud": set()}


# ---------------------------------------------------------------- 引擎与校验器

def test_validate_rules_accepts_presence_kinds_without_params():
    out = engine.validate_rules([
        {"kind": "node_missing_local", "target": "modal", "params": {"junk": 1}},
        {"kind": "node_missing_cloud", "target": "local"},
    ])
    assert out[0]["params"] == {} and out[0]["target"] == "modal"
    assert out[1]["params"] == {} and out[1]["target"] == "local"


def _patch_engine(monkeypatch, rules):
    from app.core.config import AppConfig, BridgeConfig

    bridge = BridgeConfig(rules=rules)
    monkeypatch.setattr(engine, "get_settings", lambda: AppConfig(bridge=bridge))
    monkeypatch.setattr(engine, "modal_available", lambda: True)


def _facts(classes: list[str]) -> dict:
    return {"gen_type": "image", "video_nodes": [], "image_nodes": classes, "max_width": None,
            "max_height": None, "class_types": classes, "model_refs": []}


async def test_engine_node_missing_local_routes_to_modal(monkeypatch):
    from app.routing.engine import decide

    _patch_engine(monkeypatch, [{"kind": "node_missing_local", "target": "modal", "name": "本地缺节点"}])
    d = decide(_facts(["VHS_VideoCombine", "KSampler"]),
               presence={"local": {"KSampler", "SaveImage"}, "cloud": {"VHS_VideoCombine"}})
    assert d.backend == "modal"
    row = next(t for t in d.trace if t["rule"] == "custom:本地缺节点")
    assert row["matched"] is True and "VHS_VideoCombine" in row["detail"]


async def test_engine_node_missing_cloud_skips_when_cache_not_ready(monkeypatch):
    from app.routing.engine import decide

    _patch_engine(monkeypatch, [{"kind": "node_missing_cloud", "target": "local", "name": "云端缺节点"}])
    d = decide(_facts(["KSampler"]), presence={"local": {"KSampler"}, "cloud": set()})
    assert d.backend == "local"  # 未命中 → default（local）
    row = next(t for t in d.trace if t["rule"] == "custom:云端缺节点")
    assert row["matched"] is False and "未就绪" in row["detail"]


async def test_engine_node_missing_cloud_keeps_local(monkeypatch):
    from app.routing.engine import decide

    _patch_engine(monkeypatch, [{"kind": "node_missing_cloud", "target": "local", "name": "云端缺节点"}])
    d = decide(_facts(["SomeLocalOnlyNode"]),
               presence={"local": {"SomeLocalOnlyNode", "KSampler"}, "cloud": {"KSampler"}})
    assert d.backend == "local"
    row = next(t for t in d.trace if t["rule"] == "custom:云端缺节点")
    assert row["matched"] is True and "SomeLocalOnlyNode" in row["detail"]

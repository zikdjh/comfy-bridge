"""路由引擎单元测试（不依赖真实配置/Modal：monkeypatch get_settings 与 modal_available）。"""

import pytest

from app.routing import engine


class _Bridge:
    def __init__(self, **kw):
        self.video_backend = kw.get("video_backend", "modal")
        self.large_side_pixels = kw.get("large_side_pixels", 0)
        self.default_backend = kw.get("default_backend", "local")
        self.on_modal_unavailable = kw.get("on_modal_unavailable", "fallback_local")


class _Settings:
    def __init__(self, bridge: _Bridge | None = None):
        self.bridge = bridge or _Bridge()


@pytest.fixture()
def patch_engine(monkeypatch):
    def _patch(bridge: _Bridge | None = None, modal_ok: bool = True):
        monkeypatch.setattr(engine, "get_settings", lambda: _Settings(bridge))
        monkeypatch.setattr(engine, "modal_available", lambda: modal_ok)
        monkeypatch.setattr(engine.runner_client, "deployed_state", lambda: "deployed")
        return bridge or _Bridge()

    return _patch


def _graph(*nodes: dict) -> dict:
    return {str(i): n for i, n in enumerate(nodes)}


# ---------------------------------------------------------------- classify_graph
def test_classify_video_nodes():
    facts = engine.classify_graph(
        _graph(
            {"class_type": "VHS_VideoCombine", "inputs": {}},
            {"class_type": "KSampler", "inputs": {"seed": 1}},
        )
    )
    assert facts["gen_type"] == "video"
    assert facts["video_nodes"] == ["VHS_VideoCombine"]


def test_classify_size_and_model_refs():
    facts = engine.classify_graph(
        _graph(
            {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 768}},
            {"class_type": "KSampler", "inputs": {"seed": 1, "lora_name": "add.safetensors"}},
        )
    )
    assert facts["max_width"] == 1024
    assert facts["max_height"] == 768
    assert "add.safetensors" in facts["model_refs"]


def test_classify_empty_graph():
    facts = engine.classify_graph({})
    assert facts["gen_type"] == "none"
    assert facts["max_width"] is None
    assert facts["model_refs"] == []


# ---------------------------------------------------------------- decide 规则链
def test_manual_backend_wins(patch_engine):
    patch_engine()
    d = engine.decide(engine.classify_graph({}), manual_backend="modal")
    assert d.backend == "modal"
    assert d.trace[0]["rule"] == "manual" and d.trace[0]["matched"] is True


def test_tag_cloud_prefix(patch_engine):
    patch_engine()
    d = engine.decide(engine.classify_graph({}), workflow_name="Cloud:flux 人像")
    assert d.backend == "modal"
    assert any(t["rule"] == "tag" and t["matched"] for t in d.trace)


def test_cloud_only_model_routes_modal(patch_engine):
    patch_engine()
    d = engine.decide(
        engine.classify_graph({}), models={"cloud_only": ["flux.safetensors"], "missing": []}
    )
    assert d.backend == "modal"
    assert any(t["rule"] == "cloud_model" and t["matched"] for t in d.trace)


def test_video_backend_local_skips_to_default(patch_engine):
    patch_engine(_Bridge(video_backend="local"))
    d = engine.decide(engine.classify_graph(_graph({"class_type": "SaveAnimatedWEBP", "inputs": {}})))
    assert d.backend == "local"  # default_backend=local 兜底
    video_step = next(t for t in d.trace if t["rule"] == "gen_type")
    assert video_step["matched"] is False


def test_size_threshold(patch_engine):
    patch_engine(_Bridge(large_side_pixels=1024))
    big = engine.classify_graph(_graph({"class_type": "EmptyLatentImage", "inputs": {"width": 2048, "height": 1024}}))
    assert engine.decide(big).backend == "modal"
    patch_engine(_Bridge(large_side_pixels=0))  # 阈值 0 = 关闭
    assert engine.decide(big).backend == "local"


def test_default_backend(patch_engine):
    patch_engine(_Bridge(default_backend="modal"))
    assert engine.decide(engine.classify_graph({})).backend == "modal"


# ---------------------------------------------------------------- 可用性回退
def test_fallback_local_when_modal_unavailable(patch_engine):
    patch_engine(modal_ok=False)
    d = engine.decide(engine.classify_graph({}), manual_backend="modal")
    assert d.backend == "modal"
    assert d.final_backend == "local"
    assert d.fallback
    assert d.trace[-1]["rule"] == "fallback"


def test_reject_when_modal_unavailable(patch_engine):
    patch_engine(_Bridge(on_modal_unavailable="reject"), modal_ok=False)
    d = engine.decide(engine.classify_graph({}), manual_backend="modal")
    assert d.final_backend == ""
    assert d.fallback


def test_no_fallback_when_local(patch_engine):
    patch_engine(modal_ok=False)
    d = engine.decide(engine.classify_graph({}))  # default local，不触发回退
    assert d.final_backend == "local"
    assert d.fallback is None

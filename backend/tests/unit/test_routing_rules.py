"""M3-1 自定义路由规则链：validate_rules 校验器 + decide 自定义链求值 + 内置链兼容。

get_settings 一律 patch——不读真实 ~/.comfybridge/config.toml。
"""

import pytest

from app.core.config import AppConfig, BridgeConfig
from app.routing import engine


def _patch_bridge(monkeypatch, **bridge_kwargs) -> BridgeConfig:
    bridge = BridgeConfig(**bridge_kwargs)
    monkeypatch.setattr(engine, "get_settings", lambda: AppConfig(bridge=bridge))
    monkeypatch.setattr(engine, "modal_available", lambda: True)  # 决策断言与回退分支解耦
    return bridge


def _facts(gen_type: str = "image", side: int | None = None) -> dict:
    return {
        "gen_type": gen_type,
        "video_nodes": ["VHS_VideoCombine"] if gen_type == "video" else [],
        "image_nodes": ["SaveImage"] if gen_type == "image" else [],
        "max_width": side,
        "max_height": side,
        "class_types": [],
        "model_refs": [],
    }


# ---------------------------------------------------------------- 校验器

def test_validate_rules_normalizes_and_fills_id():
    out = engine.validate_rules([
        {"kind": " workflow_tag ", "params": {"prefix": " gpu: "}, "target": " modal ", "name": " 显卡工作流 "},
        {"kind": "min_pixels", "params": {"pixels": "1024"}, "target": "local", "id": "fixed-id"},
    ])
    assert out[0]["id"] and len(out[0]["id"]) == 8  # id 自动补
    assert out[0] == {"id": out[0]["id"], "name": "显卡工作流", "kind": "workflow_tag",
                      "params": {"prefix": "gpu:"}, "target": "modal", "enabled": True}
    assert out[1]["params"] == {"pixels": 1024} and out[1]["id"] == "fixed-id"


def test_validate_rules_rejects_bad_shapes():
    with pytest.raises(ValueError, match="数组"):
        engine.validate_rules({"kind": "gen_type"})
    with pytest.raises(ValueError, match="必须是对象"):
        engine.validate_rules(["gen_type"])
    with pytest.raises(ValueError, match="类型无效"):
        engine.validate_rules([{"kind": "nope", "target": "modal"}])
    with pytest.raises(ValueError, match="目标无效"):
        engine.validate_rules([{"kind": "gen_type", "params": {"gen_type": "video"}, "target": "cloud"}])
    with pytest.raises(ValueError, match="前缀不能为空"):
        engine.validate_rules([{"kind": "workflow_tag", "params": {"prefix": "  "}, "target": "modal"}])
    with pytest.raises(ValueError, match="video/image"):
        engine.validate_rules([{"kind": "gen_type", "params": {"gen_type": "audio"}, "target": "modal"}])
    with pytest.raises(ValueError, match="必须是整数"):
        engine.validate_rules([{"kind": "min_pixels", "params": {"pixels": "big"}, "target": "modal"}])
    with pytest.raises(ValueError, match="大于 0"):
        engine.validate_rules([{"kind": "min_pixels", "params": {"pixels": 0}, "target": "modal"}])
    with pytest.raises(ValueError, match="必须是对象"):
        engine.validate_rules([{"kind": "gen_type", "params": "video", "target": "modal"}])


# ---------------------------------------------------------------- 引擎：兼容性

def test_decide_empty_rules_keeps_legacy_chain(monkeypatch):
    _patch_bridge(monkeypatch, video_backend="modal", large_side_pixels=2048)
    d = engine.decide(_facts("image", side=4096))
    rules_seen = [t["rule"] for t in d.trace]
    assert "gen_type" in rules_seen and "size" in rules_seen  # 内置两步仍在
    assert d.backend == "modal"  # 大分辨率命中内置 size（历史行为不变）


# ---------------------------------------------------------------- 引擎：自定义链

def _rule(**kw) -> dict:
    base = {"kind": "workflow_tag", "params": {"prefix": "gpu:"}, "target": "modal"}
    base.update(kw)
    return base


def test_decide_custom_chain_matches_and_replaces_legacy(monkeypatch):
    _patch_bridge(monkeypatch, rules=[_rule(name="显卡上云")])
    d = engine.decide(_facts(), workflow_name="gpu:upscale")
    assert d.backend == "modal"
    labels = [t["rule"] for t in d.trace]
    assert any(l.startswith("custom:") for l in labels)
    assert "gen_type" not in labels and "size" not in labels  # 内置两步被替换


def test_decide_custom_chain_first_match_wins(monkeypatch):
    _patch_bridge(monkeypatch, rules=[
        _rule(name="标签", params={"prefix": "a:"}, target="modal"),
        {"kind": "gen_type", "params": {"gen_type": "video"}, "target": "local", "name": "视频留本地"},
    ])
    d = engine.decide(_facts("video"), workflow_name="a:demo")
    assert d.backend == "modal"
    custom_rows = [t for t in d.trace if t["rule"].startswith("custom:")]
    assert len(custom_rows) == 1  # 首条命中后不再求值后续规则


def test_decide_custom_chain_skips_disabled_and_unknown(monkeypatch):
    _patch_bridge(monkeypatch, rules=[
        _rule(name="停用", enabled=False),
        {"kind": "mystery", "params": {}, "target": "modal", "name": "坏规则"},
        {"kind": "min_pixels", "params": {"pixels": 1024}, "target": "modal", "name": "大图"},
    ])
    d = engine.decide(_facts("image", side=4096))
    assert d.backend == "modal"
    by_rule = {t["rule"]: t for t in d.trace}
    assert "已停用" in by_rule["custom:停用"]["detail"]
    assert "未知规则类型" in by_rule["custom:坏规则"]["detail"]
    assert by_rule["custom:大图"]["matched"] is True


def test_decide_custom_chain_min_pixels_threshold(monkeypatch):
    _patch_bridge(monkeypatch, rules=[_rule(kind="min_pixels", params={"pixels": 4096}, target="modal")],
                  default_backend="local")
    assert engine.decide(_facts("image", side=4095)).backend == "local"
    assert engine.decide(_facts("image", side=4096)).backend == "modal"


def test_decide_custom_chain_falls_through_to_default(monkeypatch):
    _patch_bridge(monkeypatch, rules=[_rule(params={"prefix": "cloud:"}, target="modal")],
                  default_backend="modal")
    d = engine.decide(_facts("image"), workflow_name="plain")
    assert d.backend == "modal"  # 无命中 → default_backend 兜底
    assert d.trace[-1]["rule"] == "default"


def test_decide_fixed_safety_rules_run_before_custom(monkeypatch):
    _patch_bridge(monkeypatch, rules=[_rule(kind="min_pixels", params={"pixels": 1024}, target="local")])
    d = engine.decide(_facts("image"), models={"cloud_only": ["big.safetensors"]})
    assert d.backend == "modal"  # cloud_model 固定规则命中 → 自定义链不再求值
    assert not any(t["rule"].startswith("custom:") for t in d.trace)


# ---------------------------------------------------------------- 配置序列化回环

def test_dump_toml_roundtrips_rules_list_of_dicts():
    """list[dict] 必须渲染成 TOML 内联表数组（曾退化成 Python repr 字符串，毒化整个配置）。"""
    import tomllib

    from app.api.v1.config import _dump_toml

    rule = {"id": "abc123", "name": "显卡", "kind": "workflow_tag", "params": {"prefix": "gpu:"},
            "target": "modal", "enabled": True}
    text = _dump_toml({"bridge": {"rules": [rule], "extra_launch_args": ["--fast"], "listen": "127.0.0.1:8189"}})
    data = tomllib.loads(text)
    assert data["bridge"]["rules"] == [rule]
    assert data["bridge"]["extra_launch_args"] == ["--fast"]

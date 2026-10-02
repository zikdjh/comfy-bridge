"""M2 收尾单元测试：单文件内容哈希 diff、镜像重建入口合并（固化+对齐一次 deploy）、工作流冲突对比。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1 import nodes as nodes_api
from app.api.v1 import workflows as wf_api
from app.nodes_mgr import image_rebuild as ir
from app.nodes_mgr import version_align as va
from app.sync import workflows_sync as ws_sync


# ---------------------------------------------------------------- 节点一致性判定（含单文件内容哈希）
def test_local_file_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(nodes_api, "custom_nodes_dir", lambda: tmp_path)
    f = tmp_path / "ws_image_save.py"
    f.write_bytes(b"print('hi')")
    assert len(nodes_api._local_file_hash("ws_image_save.py")) == 64
    assert nodes_api._local_file_hash("missing.py") is None


def test_diff_state_pkg_metadata_rules():
    lp = {"commit": "a", "enabled": True, "kind": "pkg"}
    cp_same = {"commit": "a", "enabled": True, "kind": "pkg"}
    cp_drift = {"commit": "b", "enabled": True, "kind": "pkg"}
    assert nodes_api._diff_state(lp, cp_same, True, None, None) == "same"
    assert nodes_api._diff_state(lp, cp_drift, True, None, None) == "changed"
    assert nodes_api._diff_state(lp, None, True, None, None) == "same"  # 清单缺失（legacy）但两端文件都在
    assert nodes_api._diff_state(lp, None, False, None, None) == "missing_cloud"
    assert nodes_api._diff_state(None, cp_same, True, None, None) == "missing_local"
    # 启用状态/形态差异也算 changed
    assert nodes_api._diff_state(lp, {"commit": "a", "enabled": False, "kind": "pkg"}, True, None, None) == "changed"
    assert nodes_api._diff_state(lp, {"commit": "a", "enabled": True, "kind": "single"}, True, None, None) == "changed"


def test_diff_state_single_file_content_hash():
    cp = {"commit": None, "enabled": True, "kind": "single", "repo": None}
    lp = {"commit": None, "enabled": True, "kind": "single"}
    assert nodes_api._diff_state(lp, cp, True, "aa11", "aa11") == "same"
    assert nodes_api._diff_state(lp, cp, True, "aa11", "bb22") == "changed"  # 哈希漂移
    # 哈希不可得（云端拉取失败）→ 退回元数据对比，不误报
    assert nodes_api._diff_state(lp, cp, True, "aa11", None) == "same"
    assert nodes_api._diff_state(lp, cp, True, None, None) == "same"


# ---------------------------------------------------------------- 镜像重建入口合并（固化 + 版本对齐一次 deploy）
@pytest.fixture()
def rebuild_env(monkeypatch):
    """打通 start_rebuild 的全部 IO：锁文件/部署/版本函数全部打桩，收集调用痕迹。"""
    calls = {"written": [], "restored": [], "pins": [], "rollbacks": []}

    monkeypatch.setattr(ir, "scan_custom_nodes", lambda: {"packages": [], "single_files": []})
    monkeypatch.setattr(ir.lockfile, "generate", lambda scan: {"nodes": [{"name": "n1"}], "comfyui_version": "v0.38.0"})
    monkeypatch.setattr(ir.lockfile, "render_install_script", lambda lock: "# script")
    monkeypatch.setattr(ir.lockfile, "backup_current", lambda: ("old-lock", "old-script"))
    monkeypatch.setattr(
        ir.lockfile, "write_outputs", lambda lock, script: calls["written"].append((lock, script))
    )
    monkeypatch.setattr(ir.lockfile, "restore", lambda *a: calls["restored"].append(a))

    async def fake_begin(label):
        # 与真实 begin_exclusive 一样把任务状态写进 deploy._JOB（start_rebuild 返回 job_state()）
        ir.deploy._JOB.update(
            state="running", started_ms=1, finished_ms=None, label=label, error=None, log_tail=[]
        )
        return ir.deploy.job_state()

    monkeypatch.setattr(ir.deploy, "begin_exclusive", fake_begin)
    monkeypatch.setattr(ir.deploy, "spawn", lambda rb: calls["rollbacks"].append(rb))
    monkeypatch.setattr(
        ir, "get_settings", lambda: SimpleNamespace(local=SimpleNamespace(comfy_root="X:/comfy"))
    )
    return calls


@pytest.fixture()
def reset_deploy_job():
    yield
    ir.deploy._JOB.update(state="idle", started_ms=None, finished_ms=None, label=None, error=None, log_tail=[])


async def test_rebuild_with_align_writes_pin_and_rolls_back_both(rebuild_env, reset_deploy_job, monkeypatch):
    monkeypatch.setattr(va, "local_version", lambda root: ("v0.39.0", "cafe"))
    monkeypatch.setattr(va, "read_pin", lambda: "v0.38.0")
    monkeypatch.setattr(va, "write_pin", lambda tag: rebuild_env["pins"].append(tag))

    job = await ir.start_rebuild(align_version=True)
    assert rebuild_env["pins"] == ["v0.39.0"]  # 对齐并入同一次重建
    assert "版本对齐" in job["label"] and "v0.39.0" in job["label"]
    assert len(rebuild_env["written"]) == 1 and len(rebuild_env["rollbacks"]) == 1

    rebuild_env["rollbacks"][0]()  # 模拟 deploy 失败：锁与 pin 都要回滚
    assert rebuild_env["pins"][-1] == "v0.38.0"
    assert rebuild_env["restored"] == [("old-lock", "old-script")]


async def test_rebuild_align_noop_when_pin_matches(rebuild_env, reset_deploy_job, monkeypatch):
    monkeypatch.setattr(va, "local_version", lambda root: ("v0.38.0", None))
    monkeypatch.setattr(va, "read_pin", lambda: "v0.38.0")
    monkeypatch.setattr(va, "write_pin", lambda tag: rebuild_env["pins"].append(tag))

    job = await ir.start_rebuild(align_version=True)
    assert rebuild_env["pins"] == []  # pin 已一致：不写不回滚
    assert "版本对齐" not in job["label"]
    assert len(rebuild_env["written"]) == 1  # 固化照常


async def test_rebuild_without_align_skips_version(rebuild_env, reset_deploy_job, monkeypatch):
    def _no(*a, **kw):
        raise AssertionError("align_version=False 不应触碰版本函数")

    monkeypatch.setattr(va, "local_version", _no)
    monkeypatch.setattr(va, "write_pin", _no)
    job = await ir.start_rebuild(align_version=False)
    assert "版本对齐" not in job["label"] and len(rebuild_env["written"]) == 1


async def test_rebuild_exclusive_conflict_mutates_nothing(rebuild_env, monkeypatch):
    async def busy(label):
        raise RuntimeError("已有镜像部署任务在进行中")

    monkeypatch.setattr(ir.deploy, "begin_exclusive", busy)
    with pytest.raises(RuntimeError):
        await ir.start_rebuild(align_version=True)
    assert rebuild_env["written"] == [] and rebuild_env["pins"] == []  # 互斥拒绝时不落盘


# ---------------------------------------------------------------- 工作流冲突对比
def test_summarize_ui_and_api_formats():
    ui = {"nodes": [
        {"type": "KSampler", "widgets_values": [123, "fixed"]},
        {"type": "CheckpointLoaderSimple", "widgets_values": ["sd_xl.safetensors"]},
        {"type": "LoraLoader", "widgets_values": ["add_detail.safetensors", 0.8]},
    ]}
    s = wf_api._summarize_workflow(ui)
    assert s["ok"] and s["node_count"] == 3
    assert s["models"] == ["add_detail.safetensors", "sd_xl.safetensors"]

    api = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux.gguf"}},
           "2": {"class_type": "SaveImage", "inputs": {}}}
    s2 = wf_api._summarize_workflow(api)
    assert s2["node_count"] == 2 and s2["models"] == ["flux.gguf"]
    assert wf_api._summarize_workflow({"unrelated": 1})["node_count"] == 0


def test_side_meta(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"nodes": [{"type": "SaveImage"}]}', encoding="utf-8")
    meta = wf_api._side_meta(p, with_mtime=True)
    assert meta["exists"] and len(meta["hash"]) == 12
    assert meta["node_count"] == 1 and "mtime" in meta

    bad = tmp_path / "b.json"
    bad.write_text("<not json>", encoding="utf-8")
    meta_bad = wf_api._side_meta(bad, with_mtime=False)
    assert meta_bad["exists"] and "summary_error" in meta_bad
    assert wf_api._side_meta(tmp_path / "nope.json", False) == {"exists": False}


async def test_compare_local_only_when_unconfigured(monkeypatch, tmp_path):
    (tmp_path / "x.json").write_text('{"nodes": [{"type": "KSampler"}]}', encoding="utf-8")
    monkeypatch.setattr(wf_api, "workflows_dir", lambda: tmp_path)
    monkeypatch.setattr(ws_sync, "modal_configured", lambda: False)

    data = await wf_api.workflows_compare(name="x.json")
    assert data["local"]["exists"] and data["local"]["node_count"] == 1
    assert data["cloud"] == {"exists": False, "error": "未配置 Modal 凭证"}
    assert data["identical"] is False

    with pytest.raises(Exception):  # noqa: B017 —— 非法文件名 422
        await wf_api.workflows_compare(name="../secret.json")


async def test_compare_with_cloud_via_volume(monkeypatch, tmp_path):
    local_body = '{"nodes": [{"type": "KSampler"}]}'
    cloud_body = '{"nodes": [{"type": "KSampler"}, {"type": "VAEDecode"}]}'
    (tmp_path / "x.json").write_text(local_body, encoding="utf-8")
    monkeypatch.setattr(wf_api, "workflows_dir", lambda: tmp_path)
    monkeypatch.setattr(ws_sync, "modal_configured", lambda: True)

    async def fake_volume_get(*args, **kw):
        # volume get <vol> <remote> <dest> --force：把云端内容写到 dest（args[4]）
        dest = Path(args[4])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cloud_body, encoding="utf-8")

    monkeypatch.setattr(wf_api.volume_cli, "run", fake_volume_get)
    data = await wf_api.workflows_compare(name="x.json")
    assert data["cloud"]["exists"] and data["cloud"]["node_count"] == 2
    assert data["identical"] is False
    assert {t for t, _ in data["local"]["types"]} != {t for t, _ in data["cloud"]["types"]}

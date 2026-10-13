"""提交前预校验单元测试（M3-5 余项）：扫描/供给策略/自动供给，全部纯函数 + 假卷上传。"""

import pytest

from app.proxy import precheck


def _prompt(nodes: dict[str, dict]) -> dict:
    return {nid: {"class_type": spec[0], "inputs": spec[1]} for nid, spec in nodes.items()}


PRESENCE = {"local": {"KSampler", "VAEDecode"}, "cloud": {"KSampler", "VAEDecode", "LoraLoader"}}
LOCAL_MODELS = {"loras": {"a.safetensors"}, "checkpoints": {"sd_xl.safetensors"}}
CLOUD_MODELS = {"checkpoints": {"sd_xl.safetensors"}}


# ---------------------------------------------------------------- 引用提取
def test_extract_ref_categories_known_and_fallback():
    prompt = _prompt({
        "1": ("CheckpointLoaderSimple", {"ckpt_name": "sd_xl.safetensors"}),
        "2": ("LoraLoader", {"lora_name": "sub/a.safetensors", "strength": 0.8}),
        "3": ("Weird", {"mystery_name": "foo.safetensors", "seed": 1}),  # 兜底：_name + 模型扩展名
        "4": ("Note", {"text": "sd_xl.safetensors"}),  # 非模型输入不收
    })
    refs = dict(precheck.extract_ref_categories(prompt))
    assert refs["sd_xl.safetensors"] == "checkpoints"
    assert refs["sub/a.safetensors"] == "loras"
    assert refs["foo.safetensors"] is None
    assert len(refs) == 3


# ---------------------------------------------------------------- 扫描
def test_scan_modal_missing_nodes_and_models():
    prompt = _prompt({
        "1": ("KSampler", {"ckpt_name": "sd_xl.safetensors"}),          # 云端有
        "2": ("GhostNode", {"lora_name": "a.safetensors"}),              # 节点缺 + lora 本地有
        "3": ("VAEDecode", {"vae_name": "nowhere.safetensors"}),         # 两端都缺
    })
    r = precheck.scan(prompt, "modal", presence=PRESENCE,
                      local_models=LOCAL_MODELS, cloud_models=CLOUD_MODELS)
    assert r["ok"] is False
    assert r["missing_nodes"] == ["GhostNode"]
    assert r["missing_models"] == ["nowhere.safetensors"]
    assert r["supplyable_refs"] == ["a.safetensors"]
    assert ("a.safetensors", "loras") in r["supplyable"]
    assert r["cloud_only_models"] == []


def test_scan_local_target_cloud_only_models():
    prompt = _prompt({
        "1": ("KSampler", {"ckpt_name": "cloud_only.safetensors"}),  # 云端有本地无
    })
    r = precheck.scan(prompt, "local", presence=PRESENCE,
                      local_models=LOCAL_MODELS, cloud_models={"checkpoints": {"cloud_only.safetensors"}})
    assert r["ok"] is False
    assert r["missing_models"] == []
    assert r["cloud_only_models"] == ["cloud_only.safetensors"]


def test_scan_empty_presence_skips_node_check():
    prompt = _prompt({"1": ("GhostNode", {"seed": 1})})
    r = precheck.scan(prompt, "modal", presence={"local": set(), "cloud": set()},
                      local_models={}, cloud_models={})
    assert r["missing_nodes"] == []          # 索引不可用 → 降级跳过
    assert "跳过节点校验" in r["note"]
    ok = precheck.scan(_prompt({}), "local", presence=PRESENCE,
                       local_models=LOCAL_MODELS, cloud_models=CLOUD_MODELS)
    assert ok["ok"] is True and ok["note"] is None


# ---------------------------------------------------------------- 供给策略
def _result(**kw):
    base = {"ok": False, "target": "modal", "missing_nodes": [], "missing_models": [],
            "supplyable": [("a.safetensors", "loras")], "supplyable_refs": ["a.safetensors"],
            "cloud_only_models": [], "note": None}
    base.update(kw)
    return base


def test_decide_action_off_and_ok_and_unknown_index():
    assert precheck.decide_action("off", _result()) == ("allow", None)
    assert precheck.decide_action("auto", _result(ok=True)) == ("allow", None)
    assert precheck.decide_action("strict", _result(note="索引为空")) == ("allow", None)
    assert precheck.decide_action("bogus", _result()) == ("allow", None)  # 未知取值按 off


def test_decide_action_strict_vs_ask():
    action, options = precheck.decide_action("strict", _result(missing_nodes=["GhostNode"]))
    assert action == "block" and options is None
    action, options = precheck.decide_action("ask", _result(missing_nodes=["GhostNode"]))
    assert action == "block" and options and options[0]["action"] == "install_nodes"


def test_decide_action_auto_paths():
    r = _result()  # 目标 modal，只缺可供给模型
    assert precheck.decide_action("auto", r) == ("auto_supply", None)
    r = _result(missing_nodes=["GhostNode"])
    # 缺节点不阻止 auto 先上传模型（上传后由调用方对剩余问题按 ask 拦截）
    assert precheck.decide_action("auto", r) == ("auto_supply", None)
    r = _result(missing_models=["nowhere.safetensors"], supplyable=[], supplyable_refs=[])
    action, options = precheck.decide_action("auto", r)  # 两端都缺，无法供给
    assert action == "block" and options
    r = _result(target="local", supplyable=[], supplyable_refs=[],
                cloud_only_models=["cloud_only.safetensors"])
    action, options = precheck.decide_action("auto", r)  # 本地目标不自动上传
    assert action == "block" and options


def test_mark_supply_done_and_failures():
    r = _result(missing_nodes=["GhostNode"])
    precheck.mark_supply_done(r, ["a.safetensors"])
    assert r["supplyable_refs"] == []  # 已上传，不再提示可上传
    precheck.mark_supply_failures(r, [{"ref": "a.safetensors", "error": "boom"}])
    assert r["supplyable_refs"] == []
    assert r["missing_models"] == ["a.safetensors"]
    assert r["ok"] is False


def test_blocked_message_composition():
    r = _result(missing_nodes=["GhostNode"], missing_models=["nowhere.safetensors"])
    msg = precheck.blocked_message(r)
    assert "云端（Modal）缺少节点类型 GhostNode" in msg
    assert "nowhere.safetensors" in msg
    assert "1 个模型本地有副本" in msg
    assert "未产生任何执行与费用" in msg


# ---------------------------------------------------------------- 自动供给
@pytest.fixture()
def supply_env(monkeypatch, tmp_path):
    lora_dir = tmp_path / "loras"
    lora_dir.mkdir()
    (lora_dir / "a.safetensors").write_bytes(b"x" * 32)
    monkeypatch.setattr(precheck, "model_dirs_resolved", lambda: {"loras": str(lora_dir)})
    monkeypatch.setattr(precheck.volume_cli, "volume_name", lambda: "vol-x")
    calls: list[tuple] = []

    async def fake_put(local, remote, **kw):
        calls.append((str(local), remote, kw))

    monkeypatch.setattr(precheck.volume_cli, "put_file", fake_put)
    return calls, lora_dir


@pytest.mark.asyncio
async def test_auto_supply_uploads(supply_env):
    calls, lora_dir = supply_env
    out = await precheck.auto_supply([("a.safetensors", "loras"), ("b.safetensors", "loras")])
    assert out["uploaded"] == ["a.safetensors"]
    assert out["failed"] == [{"ref": "b.safetensors", "error": f"本地副本不存在：{lora_dir / 'b.safetensors'}"}]
    assert calls == [(str(lora_dir / "a.safetensors"),
                      "models/loras/a.safetensors", {"force": True})]


@pytest.mark.asyncio
async def test_auto_supply_no_category_and_cli_error(monkeypatch, tmp_path):
    monkeypatch.setattr(precheck, "model_dirs_resolved", lambda: {"loras": str(tmp_path)})
    monkeypatch.setattr(precheck.volume_cli, "volume_name", lambda: "vol-x")
    (tmp_path / "y.safetensors").write_bytes(b"x" * 8)  # 本地有副本，才能走到 CLI 报错分支

    async def fake_put(*args, **kw):
        raise precheck.ModalCliError("put failed")

    monkeypatch.setattr(precheck.volume_cli, "put_file", fake_put)
    out = await precheck.auto_supply([("x.bin", None), ("y.safetensors", "loras")])
    assert out["failed"][0] == {"ref": "x.bin", "error": "无法确定模型分类目录"}
    assert "put failed" in out["failed"][1]["error"]
    assert out["uploaded"] == []

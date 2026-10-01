"""工作流双向同步：三方检测 / 冲突副本命名 / 基线刷新 / apply 动作与回写。"""

import asyncio

import pytest

from app.sync import workflows_rules as rules
from app.sync import workflows_sync as ws

# ---------------------------------------------------------------- 纯逻辑

def test_valid_workflow_name_rejects_paths_and_hidden():
    assert rules.valid_workflow_name("文生图.json")
    assert rules.valid_workflow_name("my.flow-1.json")
    assert not rules.valid_workflow_name("../evil.json")
    assert not rules.valid_workflow_name(".hidden.json")
    assert not rules.valid_workflow_name("a/b.json")
    assert not rules.valid_workflow_name("notjson.txt")
    assert not rules.valid_workflow_name("")


def test_conflict_copy_name_keeps_stem():
    assert rules.conflict_copy_name("文生图.json", "20260930-104500") == "文生图.conflict-20260930-104500.json"


def test_classify_rows_three_way():
    A, B, C = "hash-a", "hash-b", "hash-c"
    rows = {r["name"]: r for r in rules.classify_rows(
        local={
            "same.json": A, "local_new.json": A,
            "local_changed.json": B, "cloud_changed.json": A, "conflict.json": C,
        },
        cloud={
            "same.json": A, "cloud_new.json": B,
            "local_changed.json": A, "cloud_changed.json": B, "conflict.json": B,
        },
        base={"same.json": A, "local_changed.json": A, "cloud_changed.json": A},
    )}
    assert rows["same.json"]["state"] == rules.S_SYNCED
    assert rows["local_new.json"]["state"] == rules.S_LOCAL_NEW
    assert rows["cloud_new.json"]["state"] == rules.S_CLOUD_NEW
    # 基线==云端、本地不同 → 只有本地动过
    assert rows["local_changed.json"]["state"] == rules.S_LOCAL_CHANGED
    # 基线==本地、云端不同 → 只有云端动过
    assert rows["cloud_changed.json"]["state"] == rules.S_CLOUD_CHANGED
    # 两端都动过（基线与两端都不同）→ 冲突
    assert rows["conflict.json"]["state"] == rules.S_CONFLICT


def test_classify_rows_without_base_is_conflict():
    rows = rules.classify_rows(
        local={"x.json": "h1"}, cloud={"x.json": "h2"}, base={}
    )
    assert rows[0]["state"] == rules.S_CONFLICT
    assert rows[0]["suggested"] is None  # 冲突需人工选择


def test_classify_rows_first_sync_one_side_empty():
    # 首次同步：基线为空但只有一端有 → 不是冲突，是单向新增
    rows = rules.classify_rows(local={"a.json": "h"}, cloud={}, base={})
    assert rows[0]["state"] == rules.S_LOCAL_NEW
    assert rows[0]["suggested"] == rules.PUSH


def test_merge_state_only_refreshes_synced_rows():
    state = rules.merge_state(
        {"old.json": "h0"},
        [
            {"name": "old.json", "state": rules.S_SYNCED, "local_hash": "h1"},
            {"name": "new.json", "state": rules.S_LOCAL_NEW, "local_hash": "h2"},
        ],
    )
    assert state == {"old.json": "h1"}  # 未同步过的新文件不进基线


# ---------------------------------------------------------------- apply（假卷）

class _FakeVolume:
    """模拟数据卷 user/default/workflows/ 的 get/put。"""

    def __init__(self, cloud: dict[str, bytes]):
        self.cloud = dict(cloud)

    async def run(self, *args: str) -> str:
        from pathlib import Path

        cmd, sub = args[0], args[1]
        if cmd == "volume" and sub == "get":
            remote, dest = args[3], args[4]
            name = remote.rsplit("/", 1)[-1]
            Path(dest).write_bytes(self.cloud[name])
            return ""
        if cmd == "volume" and sub == "put":
            local, remote = args[3], args[4]
            name = remote.rsplit("/", 1)[-1]
            self.cloud[name] = Path(local).read_bytes()
            return ""
        raise AssertionError(f"unexpected cli call {args}")


@pytest.fixture
def wf_env(tmp_path, monkeypatch):
    local_dir = tmp_path / "workflows"
    local_dir.mkdir()
    monkeypatch.setattr(ws, "workflows_dir", lambda: local_dir)
    monkeypatch.setattr(ws, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(ws, "modal_configured", lambda: True)
    fake = _FakeVolume({})
    monkeypatch.setattr(ws.volume_cli, "run", fake.run)

    async def fake_list_dir(remote_path, volume=None):
        if remote_path != ws.CLOUD_DIR:
            raise AssertionError(f"unexpected list_dir {remote_path}")
        return [
            {"name": name, "type": "file", "size": str(len(data))}
            for name, data in sorted(fake.cloud.items())
        ]

    monkeypatch.setattr(ws.volume_cli, "list_dir", fake_list_dir)
    return ws, local_dir, fake


def test_apply_push_pull_and_state(wf_env, tmp_path):
    ws, local_dir, fake = wf_env
    (local_dir / "a.json").write_text('{"local": 1}', encoding="utf-8")

    res = asyncio.run(ws.apply([{"name": "a.json", "action": "push"}]))
    assert res["ok"] and res["results"][0]["ok"]
    assert "a.json" in fake.cloud  # 已上卷
    assert ws.read_state()["a.json"] == ws.local_hashes()["a.json"]  # 基线推进

    # 云端被第三方修改 → pull 覆盖本地，基线记云端 hash
    fake.cloud["a.json"] = b'{"cloud": 2}'
    res = asyncio.run(ws.apply([{"name": "a.json", "action": "pull"}]))
    assert res["ok"]
    assert (local_dir / "a.json").read_text(encoding="utf-8") == '{"cloud": 2}'
    assert ws.read_state()["a.json"] == ws.local_hashes()["a.json"]


def test_apply_keep_both_creates_conflict_copy(wf_env):
    ws, local_dir, fake = wf_env
    local_bytes = b'{"v": "local"}'
    (local_dir / "x.json").write_bytes(local_bytes)
    fake.cloud["x.json"] = b'{"v": "cloud"}'

    res = asyncio.run(ws.apply([{"name": "x.json", "action": "keep_both"}]))
    assert res["ok"]
    copy_name = res["results"][0]["conflict_copy"]
    assert "conflict-" in copy_name and copy_name.endswith(".json")
    assert (local_dir / copy_name).read_bytes() == b'{"v": "cloud"}'  # 云端版留本地
    assert (local_dir / "x.json").read_bytes() == local_bytes  # 本地版不动
    assert fake.cloud["x.json"] == local_bytes  # 本地版推上云端
    assert ws.read_state()["x.json"] == ws.local_hashes()["x.json"]


def test_apply_validates_names_and_missing_files(wf_env):
    ws, _local_dir, _fake = wf_env
    res = asyncio.run(ws.apply([
        {"name": "../evil.json", "action": "push"},
        {"name": "no-local.json", "action": "push"},
        {"name": "no-cloud.json", "action": "pull"},
        {"name": "a.json", "action": "explode"},
    ]))
    assert not res["ok"]
    errors = {r["name"]: r["error"] for r in res["results"]}
    assert errors["../evil.json"] == "非法文件名"
    assert errors["no-local.json"] == "本地不存在"
    assert errors["no-cloud.json"] == "云端不存在"
    assert "未知动作" in errors["a.json"]


def test_apply_pull_cloud_missing(wf_env):
    ws, _local_dir, _fake = wf_env
    res = asyncio.run(ws.apply([{"name": "nope.json", "action": "pull"}]))
    assert not res["ok"]
    assert res["results"][0]["error"] == "云端不存在"


def test_status_counts_and_baseline_refresh(wf_env, monkeypatch):
    ws, local_dir, fake = wf_env
    (local_dir / "same.json").write_bytes(b"A")
    fake.cloud["same.json"] = b"A"
    fake.cloud["cloud_only.json"] = b"B"

    async def scenario():
        return await ws.status()

    st = asyncio.run(scenario())
    assert st["ok"] and st["rows"]
    counts = st["counts"]
    assert counts.get("synced") == 1 and counts.get("cloud_new") == 1
    # 状态查询顺带刷新基线：synced 行有基线，cloud_new 不进
    assert ws.read_state() == {"same.json": ws.local_hashes()["same.json"]}

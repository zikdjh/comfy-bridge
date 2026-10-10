"""版本对齐与共享部署任务行为：pin 读写 / tag 归一化 / drift 判定 / deploy 成败与回滚 / 对齐历史留痕。"""

import asyncio
import json

import pytest

from app.nodes_mgr import deploy, version_align


@pytest.fixture
def align_env(tmp_path, monkeypatch):
    """锁文件与历史文件指到临时目录，部署任务状态与收尾回调复位。"""
    lock = tmp_path / "lock" / "comfyui_version.txt"
    monkeypatch.setattr(version_align, "LOCK_FILE", lock)
    monkeypatch.setattr(version_align, "HISTORY_FILE", tmp_path / "align_history.jsonl")
    monkeypatch.setattr(deploy, "_SINK", None)
    deploy._JOB.update(
        state="idle", started_ms=None, finished_ms=None,
        label=None, error=None, log_tail=[],
    )
    return lock


def _comfy_root(tmp_path, version):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "comfyui_version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    return root


def test_tag_for_normalizes_prefix():
    assert version_align.tag_for("0.38.0") == "v0.38.0"
    assert version_align.tag_for("v0.38.0") == "v0.38.0"
    assert version_align.tag_for(" 0.38.0 ") == "v0.38.0"


def test_status_detects_drift(tmp_path, align_env):
    st = version_align.status(_comfy_root(tmp_path, "0.38.0"))
    assert st["local_version"] == "0.38.0"
    assert st["pinned"] == version_align.DEFAULT_PIN
    assert st["tag"] == "v0.38.0"
    assert st["drift"] is True


def test_status_without_local_install(tmp_path, align_env):
    st = version_align.status(tmp_path / "nope")
    assert st["local_version"] is None
    assert st["tag"] is None
    assert st["drift"] is False


def test_read_pin_falls_back_to_default(tmp_path, align_env):
    assert version_align.read_pin() == version_align.DEFAULT_PIN
    version_align.write_pin("v0.99.0")
    assert version_align.read_pin() == "v0.99.0"


class _FakeProc:
    def __init__(self, out=b"", err=b"", returncode=0, slow=False):
        self._out, self._err, self._slow = out, err, slow
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        if self._slow:
            await asyncio.sleep(30)
        return self._out, self._err

    def kill(self):
        self.killed = True


def _patch_deploy_proc(monkeypatch, proc):
    async def _call(*args, **kwargs):
        return proc
    monkeypatch.setattr(deploy.asyncio, "create_subprocess_exec", _call)


def test_start_align_deploys_and_keeps_pin(tmp_path, align_env, monkeypatch):
    proc = _FakeProc(out=b"Deployed apps\n")
    _patch_deploy_proc(monkeypatch, proc)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.38.0"))
        await asyncio.sleep(0)  # 放行部署任务

    asyncio.run(scenario())
    assert align_env.read_text(encoding="utf-8").strip() == "v0.38.0"
    job = deploy.job_state()
    assert job["state"] == "ok"
    assert "v0.38.0" in job["label"]
    assert "Deployed apps" in "".join(job["log_tail"])


def test_start_align_noop_when_pin_matches(tmp_path, align_env):
    root = _comfy_root(tmp_path, "0.38.0")
    version_align.write_pin("v0.38.0")
    job = asyncio.run(version_align.start_align(root))
    assert job["state"] == "ok"
    assert "无需重建" in job["log_tail"][0]


def test_start_align_requires_local_version(tmp_path, align_env):
    with pytest.raises(ValueError):
        asyncio.run(version_align.start_align(tmp_path / "empty"))


def test_start_align_rejects_while_deploy_running(tmp_path, align_env):
    deploy._JOB.update(state="running", label="x")
    with pytest.raises(RuntimeError):
        asyncio.run(version_align.start_align(_comfy_root(tmp_path, "0.40.0")))


def test_deploy_failure_rolls_back_pin(tmp_path, align_env, monkeypatch):
    version_align.write_pin("v0.38.0")
    proc = _FakeProc(err=b"fatal: Remote branch v0.99.0 not found\n", returncode=128)
    _patch_deploy_proc(monkeypatch, proc)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.99.0"))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert version_align.read_pin() == "v0.38.0"
    job = deploy.job_state()
    assert job["state"] == "error"
    assert "回滚" in job["error"]
    assert any("not found" in ln for ln in job["log_tail"])


def test_deploy_timeout_keeps_new_pin(tmp_path, align_env, monkeypatch):
    monkeypatch.setattr(deploy, "_DEPLOY_TIMEOUT", 0.02)
    version_align.write_pin("v0.37.4")
    proc = _FakeProc(slow=True)
    _patch_deploy_proc(monkeypatch, proc)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.38.0"))
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert proc.killed
    job = deploy.job_state()
    assert job["state"] == "error"
    assert "超时" in job["error"]
    assert "modal app list" in job["error"]
    assert "modal image logs" in job["error"]
    assert "不要重复发起 deploy" in job["error"]
    # 超时属不确定态（镜像可能已建成）：不回滚
    assert version_align.read_pin() == "v0.38.0"


def test_deploy_cli_missing_rolls_back_pin(tmp_path, align_env, monkeypatch):
    version_align.write_pin("v0.37.4")

    async def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("modal")

    monkeypatch.setattr(deploy.asyncio, "create_subprocess_exec", raise_fnf)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.38.0"))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert version_align.read_pin() == "v0.37.4"
    assert "无法启动 modal CLI" in deploy.job_state()["error"]


# ---------------------------------------------------------------- 对齐历史留痕

def test_align_ok_appends_history(tmp_path, align_env, monkeypatch):
    proc = _FakeProc(out=b"Deployed apps\n")
    _patch_deploy_proc(monkeypatch, proc)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.38.0"))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    hist = version_align.read_history()
    assert len(hist) == 1
    rec = hist[0]
    assert rec["trigger"] == "align"
    assert rec["from_pin"] == version_align.DEFAULT_PIN and rec["to_tag"] == "v0.38.0"
    assert rec["outcome"] == "ok" and rec["error"] is None
    assert isinstance(rec["duration_ms"], int) and rec["duration_ms"] >= 0
    assert rec["time"] and rec["ts_ms"] > 0
    assert deploy._SINK is None  # 一次性回调已 fire 清除


def test_align_rollback_records_history(tmp_path, align_env, monkeypatch):
    version_align.write_pin("v0.38.0")
    proc = _FakeProc(err=b"fatal: Remote branch v0.99.0 not found\n", returncode=128)
    _patch_deploy_proc(monkeypatch, proc)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.99.0"))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    rec = version_align.read_history()[0]
    assert rec["outcome"] == "rolled_back"
    assert "退出码 128" in rec["error"]
    assert rec["from_pin"] == "v0.38.0" and rec["to_tag"] == "v0.99.0"


def test_align_timeout_records_history(tmp_path, align_env, monkeypatch):
    monkeypatch.setattr(deploy, "_DEPLOY_TIMEOUT", 0.02)
    version_align.write_pin("v0.37.4")
    proc = _FakeProc(slow=True)
    _patch_deploy_proc(monkeypatch, proc)

    async def scenario():
        await version_align.start_align(_comfy_root(tmp_path, "0.38.0"))
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    rec = version_align.read_history()[0]
    assert rec["outcome"] == "timeout"
    assert "超时" in rec["error"]
    assert rec["to_tag"] == "v0.38.0"  # 不确定态：pin 保留，留痕如实记录


def test_align_noop_records_history_without_sink(tmp_path, align_env):
    root = _comfy_root(tmp_path, "0.38.0")
    version_align.write_pin("v0.38.0")
    asyncio.run(version_align.start_align(root))
    rec = version_align.read_history()[0]
    assert rec["outcome"] == "noop" and rec["from_pin"] == rec["to_tag"] == "v0.38.0"
    assert deploy._SINK is None  # noop 不经 deploy 收尾，无回调注册


def test_read_history_newest_first_and_skips_corrupt(tmp_path, align_env):
    rec_new = version_align._record("align", "v0.37.4", "v0.38.0", "ok")
    rec_old = version_align._record("align", "v0.38.0", "v0.38.1", "rolled_back", error="x")
    version_align.HISTORY_FILE.write_text(
        "{不是json\n" + json.dumps(rec_old, ensure_ascii=False) + "\n" + json.dumps(rec_new, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    hist = version_align.read_history()
    assert [h["to_tag"] for h in hist] == ["v0.38.0", "v0.38.1"]  # 新的在前，坏行跳过


def test_append_history_silent_when_unwritable(tmp_path, align_env, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(version_align, "HISTORY_FILE", blocker / "sub" / "history.jsonl")
    version_align.append_history({"outcome": "ok"})  # mkdir 撞上普通文件 → OSError 被吞
    assert version_align.read_history() == []

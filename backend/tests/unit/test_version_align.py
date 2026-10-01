"""版本对齐与共享部署任务行为：pin 读写 / tag 归一化 / drift 判定 / deploy 成败与回滚。"""

import asyncio

import pytest

from app.nodes_mgr import deploy, version_align


@pytest.fixture
def align_env(tmp_path, monkeypatch):
    """锁文件指到临时目录，部署任务状态复位。"""
    lock = tmp_path / "lock" / "comfyui_version.txt"
    monkeypatch.setattr(version_align, "LOCK_FILE", lock)
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

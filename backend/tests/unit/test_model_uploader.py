"""模型分块上传：会话持久化、断点、MD5 校验、落卷与恢复。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.models_mgr import uploader


@pytest.fixture()
def upload_env(monkeypatch, tmp_path: Path):
    root = tmp_path / "uploads"
    monkeypatch.setattr(uploader, "upload_root", lambda: root)
    monkeypatch.setattr(uploader, "_CHUNK_SIZE", 4)
    monkeypatch.setattr(uploader.modal_cli, "volume_name", lambda: "comfyui-data")
    monkeypatch.setattr(uploader, "model_dirs_resolved", lambda: {"checkpoints": str(tmp_path / "models")})
    calls: list[tuple] = []

    async def fake_put(local, remote, **kwargs):
        calls.append(((str(local), remote), kwargs))

    async def fake_refresh(force: bool = False):
        calls.append((("refresh", force), {}))
        return {}

    monkeypatch.setattr(uploader.modal_cli, "put_file", fake_put)
    monkeypatch.setattr(uploader.cloud_index, "refresh", fake_refresh)
    uploader._LOCKS.clear()
    uploader._TASKS.clear()
    return root, calls


async def _create(payload: bytes, filename: str = "demo.safetensors") -> dict:
    return await uploader.create_upload(
        filename=filename,
        size=len(payload),
        md5=hashlib.md5(payload).hexdigest(),
        category="checkpoints",
    )


@pytest.mark.asyncio
async def test_chunk_rejects_bad_size_and_md5(upload_env):
    payload = b"abcdefghij"
    session = await _create(payload)

    with pytest.raises(ValueError, match="分块大小不匹配"):
        await uploader.write_chunk(session["id"], 0, b"abc", hashlib.md5(b"abc").hexdigest())
    with pytest.raises(ValueError, match="分块 MD5 不匹配"):
        await uploader.write_chunk(session["id"], 0, payload[:4], "0" * 32)


@pytest.mark.asyncio
async def test_resume_only_requires_missing_chunks(upload_env):
    payload = b"abcdefghij"
    session = await _create(payload)
    await uploader.write_chunk(session["id"], 0, payload[:4], hashlib.md5(payload[:4]).hexdigest())
    await uploader.write_chunk(session["id"], 1, payload[4:8], hashlib.md5(payload[4:8]).hexdigest())

    state = await uploader.get_upload(session["id"])
    assert state["uploaded_chunks"] == [0, 1]
    assert state["missing_chunks"] == [2]
    assert state["ready"] is False

    with pytest.raises(ValueError, match="仍有 1 个分块"):
        await uploader.complete_upload(session["id"])


@pytest.mark.asyncio
async def test_complete_merges_verifies_and_pushes(upload_env):
    root, calls = upload_env
    payload = b"abcdefghij"
    session = await _create(payload)
    for index in range(session["total_chunks"]):
        chunk = payload[index * 4:(index + 1) * 4]
        await uploader.write_chunk(
            session["id"],
            index,
            chunk,
            hashlib.md5(chunk).hexdigest(),
        )

    state = await uploader.complete_upload(session["id"])
    assert state["status"] == "verifying"
    task = uploader._TASKS.get(session["id"])
    assert task is not None
    await task

    final = await uploader.get_upload(session["id"])
    assert final["status"] == "done"
    assert final["actual_md5"] == final["md5"]
    assert final["missing_chunks"] == []
    assert not (root / session["id"] / "chunks").exists()
    assert not (root / session["id"] / "assembled.bin").exists()

    put_calls = [c for c in calls if c[0][1] == "models/checkpoints/demo.safetensors"]
    assert put_calls
    assert put_calls[0][1]["force"] is True
    assert put_calls[0][1]["timeout"] == 24 * 3600
    assert ("refresh", True) in [c[0] for c in calls]


@pytest.mark.asyncio
async def test_md5_mismatch_is_rejected_and_retryable(upload_env):
    payload = b"abcdefghij"
    session = await uploader.create_upload(
        filename="demo.safetensors",
        size=len(payload),
        md5="0" * 32,
        category="checkpoints",
    )
    for index in range(session["total_chunks"]):
        chunk = payload[index * 4:(index + 1) * 4]
        await uploader.write_chunk(session["id"], index, chunk, hashlib.md5(chunk).hexdigest())

    await uploader.complete_upload(session["id"])
    await uploader._TASKS[session["id"]]
    state = await uploader.get_upload(session["id"])
    assert state["status"] == "error"
    assert state["error_stage"] == "verify"
    assert "MD5 不匹配" in state["error"]
    with pytest.raises(ValueError, match="请删除会话"):
        await uploader.complete_upload(session["id"])


@pytest.mark.asyncio
async def test_restart_recovers_interrupted_finalize(upload_env):
    root, _calls = upload_env
    payload = b"abcdefghij"
    session = await _create(payload)
    meta = json.loads((root / session["id"] / "meta.json").read_text(encoding="utf-8"))
    meta["status"] = "pushing"
    (root / session["id"] / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    recovered = await uploader.get_upload(session["id"])
    assert recovered["status"] == "error"
    assert recovered["error_stage"] == "push"
    assert "被中断" in recovered["error"]


def test_path_and_volume_validation(upload_env):
    with pytest.raises(ValueError, match="文件名"):
        uploader._safe_filename("../secret.safetensors")
    with pytest.raises(ValueError, match="文件名"):
        uploader._safe_filename("/abs.safetensors")
    with pytest.raises(ValueError, match="目标目录"):
        uploader._safe_dest_dir("models/../secret")
    with pytest.raises(ValueError, match="目标目录"):
        uploader._safe_dest_dir("/absolute")
    with pytest.raises(ValueError, match="数据卷名"):
        uploader._validate_volume("Bad Volume")


@pytest.mark.asyncio
async def test_delete_removes_session(upload_env):
    root, _calls = upload_env
    session = await _create(b"abcd")
    assert await uploader.delete_upload(session["id"]) is True
    assert not (root / session["id"]).exists()
    with pytest.raises(FileNotFoundError):
        await uploader.get_upload(session["id"])


@pytest.mark.asyncio
async def test_prune_only_removes_not_started_sessions(upload_env):
    root, _calls = upload_env
    pending = await _create(b"abcdefgh")
    partial = await _create(b"ijklmnop")
    await uploader.write_chunk(
        partial["id"], 0, b"ijkl", hashlib.md5(b"ijkl").hexdigest()
    )

    assert uploader.prune_not_started() == 1
    assert not (root / pending["id"]).exists()
    assert (root / partial["id"]).is_dir()
    assert (await uploader.get_upload(partial["id"]))["phase"] == "in_progress"

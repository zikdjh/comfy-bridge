"""云端输入文件推送：优先走容器内 /upload/image，失败回落到 SDK 网关写卷。

真机回归（2026-10-11）：只做卷写入时，已在运行的容器拿着旧卷快照，
LoadImage 报 "Invalid image file: <name>"；必须先把文件写进容器自己的卷视图。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.runner import client as runner_client
from app.runner import executor


def _write_input(root: Path, name: str, content: bytes = b"png-bytes") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def test_push_input_prefers_container_upload(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}

    async def fake_post(filename: str, data: bytes, *, subfolder: str = "") -> dict:
        calls["args"] = (filename, data, subfolder)
        return {"name": filename}

    async def explode(*args, **kwargs):
        raise AssertionError("容器内上传成功时不应回落卷写入")

    monkeypatch.setattr(executor.runner_client, "post_input_file", fake_post)
    monkeypatch.setattr(executor.modal_cli, "put_file", explode)
    local = _write_input(tmp_path, "bridge_live_input.png")

    assert await executor._push_input("bridge_live_input.png", local) == "comfy"
    assert calls["args"] == ("bridge_live_input.png", b"png-bytes", "")


async def test_push_input_splits_subfolder(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}

    async def fake_post(filename: str, data: bytes, *, subfolder: str = "") -> dict:
        calls["args"] = (filename, subfolder)
        return {}

    monkeypatch.setattr(executor.runner_client, "post_input_file", fake_post)
    local = _write_input(tmp_path, "shots/ref.png")

    assert await executor._push_input("shots/ref.png", local) == "comfy"
    assert calls["args"] == ("ref.png", "shots")


async def test_push_input_falls_back_to_sdk_put(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple] = []

    async def failing_post(filename: str, data: bytes, *, subfolder: str = "") -> dict:
        raise RuntimeError("云端输入上传失败（HTTP 404）")

    async def fake_put(local, remote, **kwargs):
        calls.append((local, remote, kwargs))

    monkeypatch.setattr(executor.runner_client, "post_input_file", failing_post)
    monkeypatch.setattr(executor.modal_cli, "put_file", fake_put)
    local = _write_input(tmp_path, "a.png")

    assert await executor._push_input("a.png", local) == "volume"
    assert calls == [(str(local), "input/a.png", {"force": True})]


async def test_upload_inputs_uses_container_channel(monkeypatch, tmp_path: Path) -> None:
    seen: list = []

    async def fake_post(filename: str, data: bytes, *, subfolder: str = "") -> dict:
        seen.append(filename)
        return {}

    monkeypatch.setattr(executor.config, "input_dir", lambda: tmp_path)
    monkeypatch.setattr(executor.runner_client, "post_input_file", fake_post)
    _write_input(tmp_path, "bridge_live_input.png")

    payload = {
        "prompt_id": "abcd1234",
        "prompt": {
            "1": {"class_type": "LoadImage", "inputs": {"image": "bridge_live_input.png"}},
            "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
        },
    }
    await executor._upload_inputs(payload)
    assert seen == ["bridge_live_input.png"]


async def test_upload_inputs_skips_missing_and_traversal(monkeypatch, tmp_path: Path) -> None:
    seen: list = []

    async def fake_post(filename: str, data: bytes, *, subfolder: str = "") -> dict:
        seen.append(filename)
        return {}

    monkeypatch.setattr(executor.config, "input_dir", lambda: tmp_path)
    monkeypatch.setattr(executor.runner_client, "post_input_file", fake_post)
    _write_input(tmp_path, "ok.png")

    payload = {
        "prompt": {
            "1": {"class_type": "LoadImage", "inputs": {"image": "../secret.png"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "cloud_only.png"}},
            "3": {"class_type": "LoadImage", "inputs": {"image": "ok.png"}},
        }
    }
    await executor._upload_inputs(payload)
    assert seen == ["ok.png"]


async def test_post_input_file_sends_multipart(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"name": "a.png", "subfolder": "s", "type": "input"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(runner_client, "web_url", lambda: "https://example.modal.run")
    monkeypatch.setattr(
        runner_client, "_client", lambda: httpx.AsyncClient(transport=transport)
    )

    out = await runner_client.post_input_file("a.png", b"payload", subfolder="s")
    assert out["name"] == "a.png"
    assert captured["url"] == "https://example.modal.run/upload/image"
    assert b"payload" in captured["body"]
    assert b'name="subfolder"' in captured["body"]
    assert b"s" in captured["body"]


async def test_post_input_file_raises_on_bad_status(monkeypatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="nope"))
    monkeypatch.setattr(runner_client, "web_url", lambda: "https://example.modal.run")
    monkeypatch.setattr(
        runner_client, "_client", lambda: httpx.AsyncClient(transport=transport)
    )

    with pytest.raises(RuntimeError, match="HTTP 404"):
        await runner_client.post_input_file("a.png", b"payload")


def test_input_dir_honors_launch_input_directory(monkeypatch, tmp_path: Path) -> None:
    """启动命令带 --input-directory 时 input_dir 以其实际目录为准（GEN-03 回归）。"""
    from app.core import config as cfg

    fake = SimpleNamespace(
        local=SimpleNamespace(
            comfy_root=str(tmp_path / "root"),
            launch_command=[
                "python", "main.py",
                "--input-directory", str(tmp_path / "shared-input"),
                "--output-directory", str(tmp_path / "shared-output"),
            ],
        )
    )
    monkeypatch.setattr(cfg, "get_settings", lambda: fake)
    assert cfg.input_dir() == tmp_path / "shared-input"


def test_input_dir_falls_back_to_comfy_root(monkeypatch, tmp_path: Path) -> None:
    from app.core import config as cfg

    fake = SimpleNamespace(
        local=SimpleNamespace(comfy_root=str(tmp_path / "root"), launch_command=["python", "main.py"])
    )
    monkeypatch.setattr(cfg, "get_settings", lambda: fake)
    assert cfg.input_dir() == tmp_path / "root" / "input"


def test_input_dir_flag_without_value_falls_back(monkeypatch, tmp_path: Path) -> None:
    from app.core import config as cfg

    fake = SimpleNamespace(
        local=SimpleNamespace(
            comfy_root=str(tmp_path / "root"), launch_command=["python", "main.py", "--input-directory"]
        )
    )
    monkeypatch.setattr(cfg, "get_settings", lambda: fake)
    assert cfg.input_dir() == tmp_path / "root" / "input"

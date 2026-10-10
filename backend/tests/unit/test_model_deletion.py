
"""模型删除保护：路径识别、引用扫描、回收站与 API 边界。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import volume as volume_api
from app.volume_mgr import deletion


@pytest.fixture()
def refs_env(monkeypatch, tmp_path: Path):
    graphs = tmp_path / "graphs"
    workflows = tmp_path / "workflows"
    graphs.mkdir()
    workflows.mkdir()
    monkeypatch.setattr(deletion.store, "GRAPH_DIR", graphs)
    monkeypatch.setattr(deletion, "workflows_dir", lambda: workflows)

    return graphs, workflows


def _client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(volume_api.router, prefix="/api/v1")
    monkeypatch.setattr(volume_api, "modal_configured", lambda: True)
    monkeypatch.setattr(
        volume_api,
        "get_settings",
        lambda: type("Settings", (), {"modal": type("Modal", (), {"volume": "comfyui-data"})()})(),
    )
    return TestClient(app)


def test_model_path_and_refs() -> None:
    assert deletion.is_model_path("models/checkpoints/demo.safetensors")
    assert deletion.is_model_path(r"models\loras\style.pt")
    assert not deletion.is_model_path("models/checkpoints/readme.txt")
    assert deletion.is_model_path("models/demo.safetensors")
    assert not deletion.is_model_path("input/demo.safetensors")

    refs = deletion.model_refs({
        "nodes": [
            {"inputs": {"ckpt_name": "checkpoints/demo.safetensors"}},
            {"inputs": {"lora_name": "style.pt"}},
            {"inputs": {"image": "input/photo.png"}},
            {"widgets_values": ["unknown_loader.safetensors"]},
        ]
    })
    assert refs == {"checkpoints/demo.safetensors", "style.pt", "unknown_loader.safetensors"}


def test_matches_bare_filename_and_relative_path() -> None:
    target = "models/checkpoints/demo.safetensors"
    assert deletion._matches("demo.safetensors", target)
    assert deletion._matches("checkpoints/demo.safetensors", target)
    assert deletion._matches("models/checkpoints/demo.safetensors", target)
    assert not deletion._matches("other.safetensors", target)
    assert not deletion._matches("", target)


def test_inspect_references_merges_three_sources_and_deduplicates(
    refs_env, monkeypatch,
) -> None:
    graphs, workflows = refs_env
    (graphs / "request-1.json").write_text(
        json.dumps({"nodes": [{"inputs": {"ckpt_name": "demo.safetensors"}}]}),
        encoding="utf-8",
    )
    (workflows / "portrait.json").write_text(
        json.dumps({"nodes": [{"inputs": {"ckpt_name": "checkpoints/demo.safetensors"}}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        deletion,
        "_request_refs",
        lambda limit=2000: [
            {"source": "request", "ref": "demo.safetensors", "detail": "请求 #7"},
            {"source": "request", "ref": "demo.safetensors", "detail": "请求 #7"},
        ],
    )

    result = deletion.inspect_references("models/checkpoints/demo.safetensors")

    assert result["checked"] is True
    assert result["referenced"] is True
    assert {hit["source"] for hit in result["refs"]} == {"request", "graph", "workflow"}
    assert len(result["refs"]) == 3
    assert result["warning"]


def test_inspect_references_skips_non_model_path(refs_env, monkeypatch) -> None:
    monkeypatch.setattr(
        deletion,
        "_request_refs",
        lambda limit=2000: pytest.fail("非模型路径不应扫描请求记录"),
    )
    result = deletion.inspect_references("input/demo.safetensors")
    assert result == {"checked": False, "referenced": False, "refs": [], "warning": None}


def test_trash_stamp_age_and_path_validation() -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    age = deletion.trash_stamp_age_days("20261003T000000Z", now=now)
    assert age is not None
    assert age == pytest.approx(7.0)
    assert deletion.trash_stamp_age_days("not-a-stamp", now=now) is None
    assert deletion.trash_stamp_age_days("20261340T000000Z", now=now) is None

    assert deletion.is_trash_path(".trash/20261003T000000Z/models/checkpoints/demo.safetensors")
    assert not deletion.is_trash_path("models/checkpoints/demo.safetensors")
    assert not deletion.is_trash_path(".trash/not-a-stamp/demo.safetensors")


@pytest.mark.asyncio
async def test_move_restore_and_cleanup_only_touch_valid_trash_paths(
    monkeypatch,
) -> None:
    moved: list[tuple[str, str, str, str]] = []

    async def fake_move_entry(volume: str, src: str, dest: str, kind: str = "file") -> None:
        moved.append((volume, src, dest, kind))

    monkeypatch.setattr(deletion, "move_entry", fake_move_entry)
    dest = await deletion.move_to_trash("comfyui-data", "models/checkpoints/demo.safetensors")
    assert dest.startswith(".trash/")
    assert dest.endswith("/models/checkpoints/demo.safetensors")
    assert moved[0][:3] == (
        "comfyui-data",
        "models/checkpoints/demo.safetensors",
        dest,
    )

    restored = await deletion.restore_from_trash(
        "comfyui-data",
        dest,
        "models/checkpoints/restored.safetensors",
    )
    assert restored == "models/checkpoints/restored.safetensors"
    assert moved[1][1] == dest

    with pytest.raises(ValueError, match="回收站本身"):
        await deletion.move_to_trash("comfyui-data", ".trash")
    with pytest.raises(ValueError, match="只能从"):
        await deletion.restore_from_trash("comfyui-data", "models/checkpoints/demo.safetensors")

    async def fake_list_dir(path: str, volume: str | None = None) -> list[dict]:
        assert path == deletion.TRASH_ROOT
        assert volume == "comfyui-data"
        return [
            {"name": "20261003T000000Z"},
            {"name": "20261009T000000Z"},
            {"name": "not-a-stamp"},
        ]

    removed: list[tuple] = []

    async def fake_run(*args: str) -> str:
        removed.append(args)
        return ""

    monkeypatch.setattr(deletion.modal_cli, "list_dir", fake_list_dir)
    monkeypatch.setattr(deletion.modal_cli, "run", fake_run)
    now = datetime(2026, 10, 10, tzinfo=UTC)
    result = await deletion.cleanup_expired_trash(
        "comfyui-data",
        retention_days=7,
        now=now,
    )
    assert result == [".trash/20261003T000000Z"]
    assert len(removed) == 1
    assert removed[0][:5] == ("volume", "rm", "comfyui-data", ".trash/20261003T000000Z", "-r")


@pytest.mark.asyncio
async def test_list_trash_returns_entries_and_marks_unreadable_timestamp(
    monkeypatch,
) -> None:
    async def fake_list_dir(path: str, volume: str | None = None) -> list[dict]:
        if path == deletion.TRASH_ROOT:
            return [
                {"name": "20261003T000000Z", "type": "dir"},
                {"name": "not-a-dir", "type": "file"},
            ]
        if path == ".trash/20261003T000000Z":
            return [
                {"name": "demo.safetensors", "type": "file", "size": "1 GiB", "mtime": "x"},
            ]
        raise AssertionError(path)

    monkeypatch.setattr(deletion.modal_cli, "list_dir", fake_list_dir)
    entries = await deletion.list_trash("comfyui-data")
    assert entries == [{
        "path": ".trash/20261003T000000Z/demo.safetensors",
        "name": "demo.safetensors",
        "type": "file",
        "size": "1 GiB",
        "mtime": "x",
        "error": None,
    }]


def test_delete_api_dry_run_and_reference_gate(monkeypatch) -> None:
    client = _client(monkeypatch)
    moved: list[tuple] = []

    async def fake_entry_kind(vol: str, rel: str) -> str:
        assert vol == "comfyui-data"
        assert rel == "models/checkpoints/demo.safetensors"
        return "file"

    async def fake_move_to_trash(vol: str, rel: str, kind: str) -> str:
        moved.append((vol, rel, kind))
        return ".trash/20261010T000000Z/models/checkpoints/demo.safetensors"

    reference = {
        "checked": True,
        "referenced": True,
        "refs": [{"source": "workflow", "ref": "demo.safetensors", "detail": "p.json"}],
        "warning": "被 1 条工作流引用",
    }
    monkeypatch.setattr(volume_api, "_entry_kind", fake_entry_kind)
    monkeypatch.setattr(volume_api.deletion, "inspect_references", lambda rel: reference)
    monkeypatch.setattr(volume_api.deletion, "move_to_trash", fake_move_to_trash)

    dry = client.post(
        "/api/v1/volume/delete",
        json={"paths": ["models/checkpoints/demo.safetensors"], "dry_run": True},
    )
    assert dry.status_code == 200
    assert dry.json()["results"][0]["reference"] == reference
    assert moved == []

    blocked = client.post(
        "/api/v1/volume/delete",
        json={"paths": ["models/checkpoints/demo.safetensors"]},
    )
    assert blocked.status_code == 200
    assert blocked.json()["ok"] is False
    assert blocked.json()["results"][0]["reference"] == reference
    assert moved == []

    allowed = client.post(
        "/api/v1/volume/delete",
        json={
            "paths": ["models/checkpoints/demo.safetensors"],
            "allow_referenced": True,
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True
    assert allowed.json()["results"][0]["trash_path"].startswith(".trash/")
    assert moved == [("comfyui-data", "models/checkpoints/demo.safetensors", "file")]


def test_trash_restore_rejects_existing_target_and_purge_guard(monkeypatch) -> None:
    client = _client(monkeypatch)
    trash_path = ".trash/20261010T000000Z/models/checkpoints/demo.safetensors"

    async def fake_entry_kind(vol: str, rel: str) -> str:
        assert rel == trash_path
        return "file"

    async def fake_list_dir(path: str, volume: str | None = None) -> list[dict]:
        assert path == "models/checkpoints"
        return [{"name": "demo.safetensors", "type": "file"}]

    monkeypatch.setattr(volume_api, "_entry_kind", fake_entry_kind)
    monkeypatch.setattr(volume_api.volume_cli, "list_dir", fake_list_dir)
    conflict = client.post(
        "/api/v1/volume/trash/restore",
        json={"path": trash_path},
    )
    assert conflict.status_code == 409
    assert "拒绝覆盖" in conflict.json()["detail"]

    calls: list[tuple] = []

    async def fake_run(*args: str) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(volume_api.volume_cli, "run", fake_run)
    rejected = client.post(
        "/api/v1/volume/trash/purge",
        json={
            "paths": [
                "models/checkpoints/demo.safetensors",
                ".secret/demo.safetensors",
            ]
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["ok"] is False
    assert len(rejected.json()["results"]) == 2
    assert calls == []

    purged = client.post(
        "/api/v1/volume/trash/purge",
        json={"paths": [trash_path]},
    )
    assert purged.status_code == 200
    assert purged.json()["ok"] is True
    assert ("volume", "rm", "comfyui-data", trash_path, "-r") in calls


"""可靠性 R3：版本迁移、升级前备份与启动自检。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.core import release_state
from app.logs import store


@pytest.fixture()
def isolated(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "data" / f"cb-release-test-{uuid.uuid4().hex[:10]}"
    root.mkdir(parents=True)
    data = root / "data"
    data.mkdir()
    version_file = root / "version.txt"
    monkeypatch.setattr(release_state, "config_dir", lambda: root / "home")
    monkeypatch.setattr(release_state, "_candidate_version_files", lambda: [version_file])
    monkeypatch.setattr(store, "DB_PATH", data / "bridge.sqlite3")
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "GRAPH_DIR", data / "graphs")
    yield root, version_file
    shutil.rmtree(root, ignore_errors=True)


def _noop_backup(*, reason: str) -> dict:
    return {"name": f"backup-{reason.rsplit(':', 1)[-1]}.zip"}


def test_first_install_and_same_version_are_idempotent(isolated):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")
    calls: list[str] = []

    first = release_state.prepare_upgrade(
        backup_factory=lambda *, reason: calls.append(reason) or {"name": "backup.zip"}
    )
    second = release_state.prepare_upgrade(
        backup_factory=lambda *, reason: calls.append(reason) or {"name": "backup.zip"}
    )

    assert first["status"] == "installed"
    assert second["status"] == "current"
    assert calls == []
    state = release_state.status()
    assert state["version"] == "v0.1.0-beta.8"
    assert len(state["history"]) == 1


def test_existing_data_without_state_is_adopted_as_upgrade(isolated, monkeypatch):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")
    database = store.DB_PATH
    conn = sqlite3.connect(database)
    try:
        conn.execute("CREATE TABLE requests(prompt_id TEXT)")
    finally:
        conn.close()
    calls: list[str] = []

    result = release_state.prepare_upgrade(
        backup_factory=lambda *, reason: calls.append(reason) or {"name": "legacy.zip"}
    )

    assert result["status"] == "legacy_upgraded"
    assert result["previous_version"] == "untracked"
    assert calls == ["automatic_before_upgrade:untracked->v0.1.0-beta.8"]
    state = release_state.status()
    assert state["previous_version"] == "untracked"
    assert state["history"][0]["legacy_adoption"] is True
    assert state["upgrade_backups"][0]["name"] == "legacy.zip"


def test_upgrade_creates_backup_before_state_change(isolated):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.7", encoding="ascii")
    release_state.prepare_upgrade(backup_factory=_noop_backup)
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")
    seen: list[str] = []

    def backup(*, reason: str) -> dict:
        # 回调执行时旧状态仍在，证明备份先于版本切换。
        assert release_state.read_state()["current_version"] == "v0.1.0-beta.7"
        seen.append(reason)
        return {"name": "ComfyBridge-backup-upgrade.zip"}

    result = release_state.prepare_upgrade(backup_factory=backup)
    assert result["status"] == "upgraded"
    assert result["backup"]["name"] == "ComfyBridge-backup-upgrade.zip"
    assert seen == ["automatic_before_upgrade:v0.1.0-beta.7->v0.1.0-beta.8"]
    state = release_state.status()
    assert state["previous_version"] == "v0.1.0-beta.7"
    assert state["upgrade_backups"][0]["name"] == "ComfyBridge-backup-upgrade.zip"


def test_upgrade_backup_failure_leaves_version_unchanged(isolated):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.7", encoding="ascii")
    release_state.prepare_upgrade(backup_factory=_noop_backup)
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")

    def fail(*, reason: str) -> dict:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        release_state.prepare_upgrade(backup_factory=fail)
    state = release_state.status()
    assert state["version"] == "v0.1.0-beta.7"
    assert state["history"][0]["version"] == "v0.1.0-beta.7"


def test_downgrade_is_recorded_as_version_change(isolated):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")
    release_state.prepare_upgrade(backup_factory=_noop_backup)
    version_file.write_text("v0.1.0-beta.7", encoding="ascii")
    calls: list[str] = []
    result = release_state.prepare_upgrade(
        backup_factory=lambda *, reason: calls.append(reason) or {"name": "old.zip"}
    )
    assert result["status"] == "version_changed"
    assert calls == ["automatic_before_upgrade:v0.1.0-beta.8->v0.1.0-beta.7"]


def test_self_check_and_corrupt_state_are_tolerated(isolated):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")
    release_state.prepare_upgrade(backup_factory=_noop_backup)
    row = release_state.record_self_check(
        ok=False,
        checks=[{"name": "database", "ok": False, "detail": "locked"}],
        error="database locked",
    )
    assert row["ok"] is False
    assert release_state.status()["last_self_check"]["error"] == "database locked"

    release_state.state_path().write_text("{broken", encoding="utf-8")
    state = release_state.status()
    # 状态文件损坏时仍能从当前包版本生成可读页面，不抛异常。
    assert state["version"] == "v0.1.0-beta.8"
    assert state["history"] == []


def test_public_status_does_not_leak_secret_fields(isolated):
    _, version_file = isolated
    version_file.write_text("v0.1.0-beta.8", encoding="ascii")
    release_state.prepare_upgrade(backup_factory=_noop_backup)
    payload = json.dumps(release_state.status(), ensure_ascii=False)
    assert "token_secret" not in payload
    assert "config.toml" not in payload


def test_bundle_version_is_read_from_package_root(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "data" / f"cb-release-path-{uuid.uuid4().hex[:10]}"
    bundle = root / "ComfyBridge-0.1.0-beta.8"
    module = bundle / "runtime" / "Lib" / "site-packages" / "app" / "core" / "release_state.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (bundle / "version.txt").write_text("v0.1.0-beta.8", encoding="ascii")
    monkeypatch.delenv("CB_BUNDLE_VERSION_FILE", raising=False)
    monkeypatch.setattr(release_state, "__file__", str(module))

    try:
        assert release_state.read_current_version() == (
            "v0.1.0-beta.8",
            bundle / "version.txt",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

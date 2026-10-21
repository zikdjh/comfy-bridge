"""可靠性 R2：在线备份、恢复校验、保留清理与路径边界。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as app_main
from app.api.v1 import maintenance as api
from app.core import config as config_mod
from app.core import job_store, maintenance
from app.logs import store


@pytest.fixture()
def isolated(monkeypatch):
    # Windows 全局 pytest 临时目录存在 ACL 异常；本模块使用仓库内短生命周期目录，
    # 避免把环境 PermissionError 误报成备份逻辑失败。
    root = Path(__file__).resolve().parents[2] / "data" / f"cb-maint-test-{uuid.uuid4().hex[:10]}"
    root.mkdir(parents=True)
    home = root / "home"
    data = root / "data"
    home.mkdir()
    data.mkdir()
    monkeypatch.setattr(config_mod, "config_dir", lambda: home)
    monkeypatch.setattr(maintenance, "config_dir", lambda: home)
    monkeypatch.setattr(maintenance, "CONFIG_PATH", home / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", data / "bridge.sqlite3")
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "GRAPH_DIR", data / "graphs")
    store._conn = None
    store.init_db()
    # lifespan 还会写版本/自检状态；与备份目录使用同一隔离根。
    monkeypatch.setattr(app_main.release_state, "config_dir", lambda: home)
    yield home, data
    # 数据库路径与连接是模块级对象；测试退出时恢复原值，避免后续用例
    # 复用指向已删除目录的旧连接。
    store.close_db()
    store._conn = None
    shutil.rmtree(root, ignore_errors=True)


def _insert(prefix: str, *, created_ms: int | None = None, status: str = "done",
            graph_file: str | None = None) -> str:
    pid = f"{prefix}-{uuid.uuid4().hex}"
    store.insert_request({
        "prompt_id": pid,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "created_ms": created_ms if created_ms is not None else int(time.time() * 1000),
        "backend": "modal",
        "final_backend": "modal",
        "gen_type": "image",
        "workflow_name": prefix,
        "model_name": None,
        "graph_hash": None,
        "graph_file": graph_file,
        "client_id": None,
        "status": status,
        "rule_trace": None,
        "error": None,
    })
    return pid


def test_backup_is_consistent_and_private_path_safe(isolated):
    home, data = isolated
    (home / "config.toml").write_text('[bridge]\ndefault_backend = "local"\n', encoding="utf-8")
    (home / "audit.log").write_text('{"event":"x"}\n', encoding="utf-8")
    (data / "graphs").mkdir()
    (data / "graphs" / "a.json").write_text('{"1": {}}', encoding="utf-8")
    meta = home / "uploads" / "abc123abc123"
    meta.mkdir(parents=True)
    (meta / "meta.json").write_text('{"id":"abc123abc123"}', encoding="utf-8")
    _insert("snapshot", graph_file="a.json")

    result = maintenance.create_backup(reason="test")
    backup = Path(result["path"])
    assert backup.is_file() and result["contains_secrets"]
    assert maintenance.validate_backup(backup)["files"] >= 4
    with zipfile.ZipFile(backup) as zf:
        assert "data/bridge.sqlite3" in zf.namelist()
        assert "config.toml" in zf.namelist()
        assert "data/graphs/a.json" in zf.namelist()
        assert "uploads/abc123abc123/meta.json" in zf.namelist()

    assert maintenance.resolve_backup(backup.name) == backup.resolve()
    for bad in ("../config.toml", "x/../a.zip", "thing.txt"):
        with pytest.raises(maintenance.BackupError):
            maintenance.resolve_backup(bad)


def test_restore_rejects_tampered_hash_and_unsafe_member(isolated):
    _, _ = isolated
    source = maintenance.create_backup(reason="test")
    backup = Path(source["path"])

    with zipfile.ZipFile(backup) as zf:
        original_manifest = json.loads(zf.read("manifest.json"))
    target_name = next(
        row["path"] for row in original_manifest["files"] if row["path"].startswith("data/")
    )
    tampered = backup.with_name("tampered.zip")
    with zipfile.ZipFile(backup) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for info in zin.infolist():
            payload = zin.read(info.filename)
            if info.filename == target_name:
                payload += b"tampered"
            zout.writestr(info, payload)
    with pytest.raises(maintenance.BackupError, match="大小|哈希"):
        maintenance.validate_backup(tampered)

    unsafe = backup.with_name("unsafe.zip")
    with zipfile.ZipFile(backup) as zin, zipfile.ZipFile(unsafe, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
        zout.writestr("../escape.txt", b"x")
        manifest = json.loads(zin.read("manifest.json"))
    manifest["files"].append({
        "path": "../escape.txt",
        "size": 1,
        "sha256": "",
    })
    # 重新生成含不安全清单项的文件，仍必须在落盘前被拒绝。
    with zipfile.ZipFile(unsafe) as zin:
        members = [(i.filename, zin.read(i.filename)) for i in zin.infolist()]
    with zipfile.ZipFile(unsafe, "w") as zout:
        for name, payload in members:
            if name == "manifest.json":
                payload = json.dumps(manifest).encode()
            zout.writestr(name, payload)
    with pytest.raises(maintenance.BackupError, match="路径"):
        maintenance.validate_backup(unsafe)


def test_restore_stages_then_applies_on_startup(isolated, monkeypatch):
    home, data = isolated
    (home / "config.toml").write_text('[bridge]\ndefault_backend = "local"\n', encoding="utf-8")
    (data / "graphs").mkdir()
    (data / "graphs" / "kept.json").write_text('{"kept": true}', encoding="utf-8")
    _insert("kept", graph_file="kept.json")
    backup = Path(maintenance.create_backup(reason="before")["path"])

    old_conn = store._conn
    if old_conn is not None:
        old_conn.close()
    store._conn = None
    (data / "bridge.sqlite3").unlink()
    (home / "config.toml").write_text('[bridge]\ndefault_backend = "modal"\n', encoding="utf-8")
    staged = maintenance.stage_restore(backup)
    assert staged["pending"]
    assert (home / maintenance.RESTORE_STAGING_NAME).is_file()
    assert (data / "bridge.sqlite3").exists()  # 暂存不热替换

    applied = maintenance.apply_pending_restore()
    assert applied and applied["ok"]
    assert not (home / maintenance.RESTORE_STAGING_NAME).exists()
    assert (home / "config.toml").read_text(encoding="utf-8").find("local") >= 0
    assert not (data / "graphs" / "added.json").exists()
    store.init_db()
    assert store.get_request_by_prompt(store.list_requests()[0]["prompt_id"])
    assert (data / "graphs" / "kept.json").is_file()


def test_restore_process_lock_rejects_concurrent_holder(isolated):
    _, _ = isolated
    with (
        maintenance._process_restore_lock(timeout=0.5),
        pytest.raises(maintenance.RestoreBusyError, match="另一个 ComfyBridge"),
        maintenance._process_restore_lock(timeout=0),
    ):
        pass


def test_restore_api_maps_process_lock_busy_to_409(isolated, monkeypatch):
    _, _ = isolated
    backup = Path(maintenance.create_backup(reason="source")["path"])
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)

    def busy(_source: Path):
        raise maintenance.RestoreBusyError(
            "另一个 ComfyBridge 实例正在校验或恢复数据"
        )

    monkeypatch.setattr(maintenance, "stage_restore", busy)
    response = client.post(
        "/api/v1/maintenance/restore",
        content=backup.read_bytes(),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 409
    assert "另一个 ComfyBridge 实例" in response.json()["detail"]


def test_lifespan_stops_when_restore_process_lock_is_busy(isolated, monkeypatch):
    _, _ = isolated

    def busy():
        raise maintenance.RestoreBusyError(
            "另一个 ComfyBridge 实例正在校验或恢复数据"
        )

    async def enter_lifespan():
        async with app_main.lifespan(app_main.app):
            pass

    monkeypatch.setattr(maintenance, "apply_pending_restore", busy)
    with pytest.raises(maintenance.RestoreBusyError):
        asyncio.run(enter_lifespan())


def test_restore_does_not_open_database_connection(isolated):
    _, data = isolated
    backup = Path(maintenance.create_backup(reason="test")["path"])
    old_conn = store._conn
    if old_conn is not None:
        old_conn.close()
    store._conn = None
    (data / "bridge.sqlite3").unlink()
    maintenance.stage_restore(backup)
    maintenance.apply_pending_restore()
    # 恢复只替换文件，真正打开连接由 lifespan 后续 init_db 负责。
    assert store._conn is None
    assert (data / "bridge.sqlite3").is_file()


def test_restore_atomically_replaces_graph_snapshot_directory(isolated):
    home, data = isolated
    graphs = data / "graphs"
    graphs.mkdir()
    (graphs / "kept.json").write_text('{"kept": true}', encoding="utf-8")
    backup = Path(maintenance.create_backup(reason="graphs")["path"])
    (graphs / "kept.json").unlink()
    (graphs / "added.json").write_text('{"added": true}', encoding="utf-8")

    old_conn = store._conn
    if old_conn is not None:
        old_conn.close()
    store._conn = None
    maintenance.stage_restore(backup)
    assert maintenance.apply_pending_restore()["ok"]
    assert (graphs / "kept.json").is_file()
    assert not (graphs / "added.json").exists()
    assert not list(data.glob(".graphs.pre-restore-*"))
    assert not list(home.glob(".uploads.pre-restore-*"))


def test_restore_rolls_back_when_a_swap_fails(isolated, monkeypatch):
    home, data = isolated
    graphs = data / "graphs"
    graphs.mkdir()
    (home / "config.toml").write_text("old-config", encoding="utf-8")
    (graphs / "old.json").write_text("old-graph", encoding="utf-8")
    backup = Path(maintenance.create_backup(reason="rollback")["path"])
    (graphs / "old.json").write_text("changed-graph", encoding="utf-8")
    (home / "config.toml").write_text("changed-config", encoding="utf-8")
    store.close_db()
    maintenance.stage_restore(backup)

    real_replace = maintenance.os.replace

    def fail_database(source, target):
        if Path(source).name == "bridge.sqlite3" and Path(target).name.startswith(
            "bridge.sqlite3.pre-restore"
        ):
            raise PermissionError("simulated database lock")
        return real_replace(source, target)

    monkeypatch.setattr(maintenance.os, "replace", fail_database)
    with pytest.raises(maintenance.BackupError, match="已回滚"):
        maintenance.apply_pending_restore()
    assert (home / "config.toml").read_text(encoding="utf-8") == "changed-config"
    assert (graphs / "old.json").read_text(encoding="utf-8") == "changed-graph"
    assert (home / maintenance.RESTORE_STAGING_NAME).is_file()


def test_restore_backup_keeps_only_ten_latest(isolated):
    _, _ = isolated
    for index in range(12):
        maintenance.create_backup(reason=f"retention-{index}")
    names = [row["name"] for row in maintenance.list_backups()]
    assert len(names) == 10
    assert "retention-0" not in str(names)
    assert "retention-1" not in str(names)


def test_stage_restore_requires_safe_pre_restore_backup(isolated, monkeypatch):
    home, _ = isolated
    backup = Path(maintenance.create_backup(reason="source")["path"])

    def fail_backup(*, reason: str):
        raise OSError("disk full")

    monkeypatch.setattr(maintenance, "create_backup", fail_backup)
    with pytest.raises(maintenance.BackupError, match="自动备份失败"):
        maintenance.stage_restore(backup)
    assert not (home / maintenance.RESTORE_STAGING_NAME).exists()


def test_backup_rejects_unlisted_extra_member(isolated):
    _, _ = isolated
    backup = Path(maintenance.create_backup(reason="strict")["path"])
    extra = backup.with_name("extra.zip")
    with zipfile.ZipFile(backup) as zin, zipfile.ZipFile(extra, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
        zout.writestr("data/graphs/not-listed.json", b"{}")
    with pytest.raises(maintenance.BackupError, match="成员与清单不一致"):
        maintenance.validate_backup(extra)


def test_concurrent_online_backups_are_consistent(isolated):
    _, _ = isolated
    _insert("concurrent")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda index: maintenance.create_backup(reason=f"parallel-{index}"),
            range(3),
        ))
    assert len({row["name"] for row in results}) == 3
    for row in results:
        assert maintenance.validate_backup(Path(row["path"]))["files"] >= 1


def test_lifespan_applies_restore_before_initializing_database(
    isolated, monkeypatch
):
    _, data = isolated
    (data / "graphs").mkdir()
    (data / "graphs" / "restored.json").write_text("{}", encoding="utf-8")
    _insert("restored-request", graph_file="restored.json")
    backup = Path(maintenance.create_backup(reason="startup")["path"])
    store.close_db()
    (data / "bridge.sqlite3").unlink()
    (data / "graphs" / "restored.json").unlink()
    maintenance.stage_restore(backup)

    calls: list[str] = []
    real_apply = maintenance.apply_pending_restore
    real_init = app_main.init_db

    def apply():
        calls.append("restore")
        store.close_db()
        return real_apply()

    def init():
        calls.append("init")
        real_init()

    def noop_start():
        return None

    async def noop_stop():
        return None

    async def noop(*_args, **_kwargs):
        return None

    class WarmTask:
        def cancel(self):
            return None

        def __await__(self):
            async def done():
                return None
            return done().__await__()

    monkeypatch.setattr(app_main.maintenance, "apply_pending_restore", apply)
    monkeypatch.setattr(app_main, "init_db", init)
    def discard_coroutine(coro):
        coro.close()
        return WarmTask()

    monkeypatch.setattr(app_main.asyncio, "create_task", discard_coroutine)
    monkeypatch.setattr(app_main, "_warm_cloud_index", noop)
    monkeypatch.setattr(app_main, "_warm_node_presence", noop)
    monkeypatch.setattr(app_main, "_warm_model_backfill", noop)
    monkeypatch.setattr(app_main, "_trash_cleanup_loop", noop)
    monkeypatch.setattr(app_main, "get_upstream_client", lambda: None)
    monkeypatch.setattr(app_main, "close_upstream_client", noop)
    monkeypatch.setattr(app_main.local_tracker, "aclose", noop)
    monkeypatch.setattr(app_main.reconcile, "start", noop_start)
    monkeypatch.setattr(app_main.reconcile, "stop", noop_stop)

    with TestClient(app_main.app):
        assert calls[:2] == ["restore", "init"]
        rows = store.list_requests()
        assert rows
        assert store.get_request_by_prompt(rows[0]["prompt_id"])

    job_store.close_default()


def test_retention_only_removes_old_finished_and_orphans(isolated):
    _, data = isolated
    old_ms = int((time.time() - 100 * 86400) * 1000)
    graphs = data / "graphs"
    graphs.mkdir()
    for name in ("old.json", "running.json", "referenced.json"):
        path = graphs / name
        path.write_text("{}", encoding="utf-8")
        old_time = time.time() - 100 * 86400
        os.utime(path, (old_time, old_time))
    referenced = _insert("referenced", created_ms=int(time.time() * 1000), graph_file="referenced.json")
    _insert("old-finished", created_ms=old_ms, status="done", graph_file="old.json")
    running = _insert("old-running", created_ms=old_ms, status="running", graph_file="running.json")
    if store._get_conn().execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone() is None:
        store._get_conn().execute(
            "CREATE TABLE jobs(namespace TEXT, job_id TEXT, payload TEXT, created_ms REAL, updated_ms INTEGER)"
        )
        store._get_conn().commit()
    store._get_conn().execute(
        "INSERT INTO jobs VALUES ('sync','done',? ,? ,?)",
        (json.dumps({"id": "done", "state": "done"}), old_ms, old_ms),
    )
    store._get_conn().execute(
        "INSERT INTO jobs VALUES ('sync','run',? ,? ,?)",
        (json.dumps({"id": "run", "state": "running"}), old_ms, old_ms),
    )
    store._get_conn().commit()

    result = maintenance.cleanup_retention(days=90)
    assert result["requests_removed"] == 1
    assert result["jobs_removed"] == 1
    assert result["graphs_removed"] == 1
    assert store.get_request_by_prompt(running)
    assert store.get_request_by_prompt(referenced)
    assert (graphs / "referenced.json").is_file()
    assert not (graphs / "old.json").exists()  # 对应请求已清理，快照成为孤立文件
    assert (graphs / "running.json").is_file()  # 运行中的请求保留引用，不删快照


def test_maintenance_api_roundtrip_and_traversal(isolated):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)

    status = client.get("/api/v1/maintenance")
    assert status.status_code == 200 and status.json()["retention"]["days"] == 90
    assert client.put("/api/v1/maintenance/retention", json={"days": 6}).status_code == 422
    assert client.put("/api/v1/maintenance/retention", json={"days": 30}).status_code == 200

    made = client.post("/api/v1/maintenance/backup", json={"reason": "api"})
    assert made.status_code == 200
    name = made.json()["name"]
    downloaded = client.get(f"/api/v1/maintenance/backups/{name}")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"
    assert client.get("/api/v1/maintenance/backups/..%2Fconfig.toml").status_code in {400, 404}
    assert client.post("/api/v1/maintenance/cleanup").status_code == 200


def test_maintenance_api_maps_disk_full_and_corrupt_database(isolated, monkeypatch):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)

    def disk_full(*, reason: str = "manual"):
        error = OSError(112, "There is not enough space on the disk")
        error.winerror = 112
        raise error

    monkeypatch.setattr(api.maintenance, "create_backup", disk_full)
    response = client.post("/api/v1/maintenance/backup", json={"reason": "test"})
    assert response.status_code == 507
    assert "磁盘空间不足" in response.json()["detail"]

    def corrupt():
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(api.maintenance, "cleanup_retention", corrupt)
    response = client.post("/api/v1/maintenance/cleanup")
    assert response.status_code == 409
    assert "数据库已损坏" in response.json()["detail"]
    assert "数据安全" in response.json()["detail"]


def test_restore_api_rejects_non_zip_and_bad_backup(isolated):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)
    assert client.post(
        "/api/v1/maintenance/restore",
        content=b"not a zip",
        headers={"Content-Type": "application/zip"},
    ).status_code == 422


def test_restore_database_has_required_schema(isolated):
    _, _ = isolated
    backup = Path(maintenance.create_backup(reason="schema")["path"])
    with zipfile.ZipFile(backup) as zf:
        payload = zf.read("data/bridge.sqlite3")
    db = maintenance.db_path().with_name("check.sqlite3")
    db.write_bytes(payload)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'"
        ).fetchone()
    finally:
        conn.close()


def test_backup_does_not_migrate_existing_database_schema(isolated):
    _, data = isolated
    store.close_db()
    database = data / "bridge.sqlite3"
    database.unlink(missing_ok=True)
    conn = sqlite3.connect(database)
    try:
        conn.execute("CREATE TABLE requests(prompt_id TEXT)")
        conn.execute("INSERT INTO requests(prompt_id) VALUES ('legacy')")
        conn.commit()
    finally:
        conn.close()

    backup = Path(maintenance.create_backup(reason="pre-upgrade")["path"])

    with zipfile.ZipFile(backup) as zf:
        payload = zf.read("data/bridge.sqlite3")
    check = data / "schema-check.sqlite3"
    check.write_bytes(payload)
    conn = sqlite3.connect(check)
    try:
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()
        ]
        assert columns == ["prompt_id"]
        assert conn.execute("SELECT prompt_id FROM requests").fetchone()[0] == "legacy"
    finally:
        conn.close()

"""零星收尾项单元测试：运行统计聚合（M2-4 余项）、容器清单 API（M4-2）、配置视图（M4-3）。"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import config as cfg_api
from app.api.v1 import launch as launch_api
from app.api.v1 import workflows as wf_api
from app.core.config import ModalConfig
from app.logs import store


# ---------------------------------------------------------------- 运行统计（store 聚合）
@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store._conn = None
    store.init_db()
    yield store
    store.close_db()


_SEQ = {"n": 0}


def _req(workflow_name: str | None, backend: str, status: str, created: datetime,
         started_s: int | None = None, dur_s: int | None = None) -> None:
    """插入一条请求并按需补 started/finished（秒级偏移拼接 duration）。"""
    _SEQ["n"] += 1
    created_ms = int(created.timestamp() * 1000)
    pid = f"pid-{created_ms}-{_SEQ['n']}"
    store.insert_request({
        "prompt_id": pid,
        "created_at": created.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "created_ms": created_ms,
        "backend": backend,
        "final_backend": backend,
        "gen_type": "image",
        "workflow_name": workflow_name,
        "model_name": None,
        "graph_hash": None,
        "graph_file": None,
        "client_id": None,
        "status": status,
        "rule_trace": None,
        "error": None,
    })
    if started_s is not None and dur_s is not None:
        with store._lock:
            store._get_conn().execute(
                "UPDATE requests SET started_ms = ?, finished_ms = ? WHERE prompt_id = ?",
                (created_ms + started_s * 1000, created_ms + (started_s + dur_s) * 1000, pid),
            )
            store._get_conn().commit()


def test_workflow_run_stats_aggregates(tmp_db):
    now = datetime.now(UTC)
    _req("SDXL文生图", "local", "done", now - timedelta(hours=1), started_s=0, dur_s=5)
    _req("SDXL文生图", "local", "done", now, started_s=0, dur_s=15)
    _req("SDXL文生图", "modal", "error", now, started_s=0, dur_s=30)
    _req("cloud:flux", "modal", "done", now, started_s=0, dur_s=60)
    _req("cloud:flux", "modal", "queued", now)  # 无 duration，不应让 AVG 变 NULL/0
    _req(None, "local", "done", now)  # 无工作流名 →（未命名）桶

    rows = {r["name"]: r for r in store.workflow_run_stats(days=7)}

    sdxl = rows["SDXL文生图"]
    assert sdxl["runs"] == 3
    assert sdxl["done"] == 2 and sdxl["errored"] == 1
    assert sdxl["modal_runs"] == 1  # local_runs 由端点层补（runs - modal_runs = 2）
    assert sdxl["avg_ms"] == round((5000 + 15000 + 30000) / 3)
    assert sdxl["max_ms"] == 30000

    flux = rows["cloud:flux"]
    assert flux["runs"] == 2 and flux["done"] == 1
    assert flux["avg_ms"] == 60000  # queued 无 duration，AVG 只算有值行

    assert rows["（未命名）"]["runs"] == 1
    assert all(r["runs"] >= 1 for r in rows.values())


def test_workflow_run_stats_days_window_and_order(tmp_db):
    now = datetime.now(UTC)
    _req("老工作流", "local", "done", now - timedelta(days=40))
    _req("热工作流", "local", "done", now)
    _req("热工作流", "local", "done", now)

    rows = store.workflow_run_stats(days=30)
    assert [r["name"] for r in rows] == ["热工作流"]  # 40 天前的被窗口过滤

    rows = store.workflow_run_stats(days=90)
    assert {r["name"] for r in rows} == {"老工作流", "热工作流"}
    assert rows[0]["runs"] == 2  # ORDER BY runs DESC


def test_workflow_run_stats_cost_only_modal(tmp_db):
    from app.logs import store as s

    now = datetime.now(UTC)
    _req("wf", "modal", "done", now)
    with s._lock:
        row = s._get_conn().execute("SELECT id FROM requests").fetchone()
        s._get_conn().execute(
            "UPDATE requests SET gpu_seconds = 60, est_cost_usd = 0.03 WHERE id = ?", (row["id"],)
        )
        s._get_conn().commit()
    _req("wf", "local", "done", now)

    (r,) = s.workflow_run_stats(days=7)
    assert r["est_cost_usd"] == pytest.approx(0.03)  # 本地请求不计成本
    assert r["modal_runs"] == 1


# ---------------------------------------------------------------- 运行统计（API 层）
@pytest.mark.asyncio
async def test_run_stats_endpoint_validation_and_rows(monkeypatch):
    async def fake_stats(days, limit):
        return [{
            "name": "wf", "runs": 3, "done": 2, "errored": 1, "modal_runs": 2,
            "avg_ms": 1000, "max_ms": 2000, "est_cost_usd": 0.05, "last_ms": 1,
        }]

    monkeypatch.setattr(wf_api.store, "aworkflow_run_stats", fake_stats)
    data = await wf_api.workflows_run_stats(days=14, limit=50)
    assert data["days"] == 14
    (row,) = data["rows"]
    assert row["local_runs"] == 1 and row["modal_runs"] == 2  # 端点层补本地/云端拆分

    with pytest.raises(HTTPException) as ei:
        await wf_api.workflows_run_stats(days=0)
    assert ei.value.status_code == 422
    with pytest.raises(HTTPException) as ei2:
        await wf_api.workflows_run_stats(days=30, limit=999)
    assert ei2.value.status_code == 422


# ---------------------------------------------------------------- 容器清单（M4-2）
@pytest.fixture()
def launch_env(monkeypatch):
    """隔离 launch 模块全局：假设置 + 缓存清零。"""
    monkeypatch.setattr(launch_api, "get_settings",
                        lambda: SimpleNamespace(modal=SimpleNamespace(app_name="myapp")))
    monkeypatch.setattr(launch_api, "_containers_cache", None)
    return launch_api


def _patch_cli(monkeypatch, rows_json: str, stop_results: list[int] | None = None):
    calls: list[list[str]] = []
    stops = iter(stop_results or [])

    def fake_run(argv, **kw):
        calls.append(argv)
        if argv[1] == "container" and argv[2] == "list":
            return SimpleNamespace(returncode=0, stdout=rows_json, stderr="")
        return SimpleNamespace(returncode=next(stops, 0), stdout="", stderr="")

    monkeypatch.setattr(launch_api.subprocess, "run", fake_run)
    return calls


def test_container_rows_ownership_filter(launch_env, monkeypatch):
    rows = json.dumps([
        {"container_id": "c1", "app_name": "myapp", "started_at": "2026-10-02T10:00:00"},
        {"container_id": "c2", "app_name": "other-app"},
        {"state": "running"},  # 缺 app_name → unknown
    ])
    _patch_cli(monkeypatch, rows)
    own, unknown, error = launch_api._container_rows()
    assert error is None and unknown == 1
    assert [r["container_id"] for r in own] == ["c1"]


def test_list_modal_containers_shape_and_cache(launch_env, monkeypatch):
    rows = json.dumps([
        {"container_id": "c1", "app_name": "myapp", "started_at": "2026-10-02T10:00:00", "gpu": "L40S"},
    ])
    calls = _patch_cli(monkeypatch, rows)

    first = launch_api._list_modal_containers()
    assert first["ok"] and first["count"] == 1
    assert first["containers"][0]["id"] == "c1"
    assert first["containers"][0]["started_at"] == "2026-10-02T10:00:00"
    assert first["containers"][0]["gpu"] == "L40S"

    launch_api._list_modal_containers()  # TTL 内第二次读取
    assert len(calls) == 1  # 不再触发 CLI


def test_list_modal_containers_empty_and_cli_error(launch_env, monkeypatch):
    _patch_cli(monkeypatch, "[]")
    empty = launch_api._list_modal_containers()
    assert empty["ok"] and empty["count"] == 0
    assert "没有运行中" in empty["detail"]

    def fake_run(argv, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="auth failed")

    monkeypatch.setattr(launch_api.subprocess, "run", fake_run)
    launch_env._containers_cache = None  # 越过 TTL 缓存，才能走到 CLI 错误分支
    bad = launch_api._list_modal_containers()
    assert not bad["ok"] and bad["count"] is None and "auth failed" in bad["detail"]


def test_stop_modal_containers_invalidates_cache(launch_env, monkeypatch):
    rows = json.dumps([{"container_id": "c1", "app_name": "myapp"}])
    calls = _patch_cli(monkeypatch, rows, stop_results=[0])

    launch_api._list_modal_containers()
    assert launch_api._containers_cache is not None

    result = launch_api._stop_modal_containers()
    assert result["ok"] and result["running"] == 1 and result["stopped"] == ["c1"]
    assert launch_api._containers_cache is None  # 停止后面板立即读到新状态
    # OPS-02/03：必须走统一 CLI argv 解析（venv Scripts 不在 PATH 时裸 "modal" 会 WinError 2），
    # 且回落解释器时自动补 `-m modal`（整合包 runtime 不带 Scripts/ 入口）。
    expected_list = launch_api.cli_argv("container", "list", "--json")
    expected_stop = launch_api.cli_argv("container", "stop", "c1", "-y")
    assert expected_list in calls
    assert expected_stop in calls
    assert all(argv[0] == expected_list[0] for argv in calls)


def test_cli_argv_falls_back_to_module_invocation(monkeypatch):
    """无 console script 时（整合包）回落 `python -m modal`，保证 CLI 通道自足。"""
    from app.modal_volume import client as volume_client

    monkeypatch.setattr(volume_client, "_EXE", None)
    monkeypatch.setattr(volume_client.sys, "executable", r"X:\bundle\runtime\python.exe")
    monkeypatch.setattr(volume_client.shutil, "which", lambda _name: None)
    argv = volume_client.cli_argv("container", "list", "--json")
    assert argv == [
        r"X:\bundle\runtime\python.exe", "-m", "modal", "container", "list", "--json",
    ]

    monkeypatch.setattr(volume_client, "_EXE", None)
    monkeypatch.setattr(volume_client.shutil, "which", lambda _name: r"C:\tools\modal.exe")
    assert volume_client.cli_argv("deploy", "runner.py") == [
        r"C:\tools\modal.exe", "deploy", "runner.py",
    ]


# ---------------------------------------------------------------- 配置视图（M4-3）
def test_modal_view_exposes_runner_fields():
    view = cfg_api._modal_view(ModalConfig(web_url="https://x.modal.run", runner_enabled=False))
    assert view["web_url"] == "https://x.modal.run"
    assert view["runner_enabled"] is False
    assert "token_secret" not in view  # 凭证明文永不回显

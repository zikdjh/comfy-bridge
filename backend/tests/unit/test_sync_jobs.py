"""同步任务注册表（M2-6）单测：作业生命周期 / 进度条目 / 取消 / 容量修剪 / API 面。

端点级测试用假 runner 与假 IO，不打真实 Modal。
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.v1 import jobs as jobs_api
from app.sync import jobs


@pytest.fixture()
def workspace_tmp_path():
    path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / f"cb-sync-test-{uuid.uuid4().hex[:10]}"
    )
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_registry():
    jobs.reset()
    yield
    jobs.reset()


async def test_job_lifecycle_ok_with_items_and_logs():
    async def runner(handle):
        handle.log("开始")
        handle.advance({"name": "a", "ok": True})
        handle.advance({"name": "b", "ok": True, "remote": "custom_nodes/b"})

    job = jobs.start_job("node_sync", "节点同步（2 项）", 2, runner)
    done = await jobs.wait_job(job["id"])
    assert done is not None
    assert done["state"] == "ok"
    assert done["done"] == 2 and done["total"] == 2
    assert [i["name"] for i in done["items"]] == ["a", "b"]
    assert any("开始" in line for line in done["logs"])
    assert done["started_ms"] is not None
    assert done["phase"] == "已完成"
    assert done["finished_ms"] is not None


async def test_job_phase_is_persisted_and_emitted():
    seen: list[str] = []

    async def runner(handle):
        handle.phase("上传节点")
        seen.append(jobs.get_job(handle._id)["phase"])
        handle.advance({"name": "a", "ok": True})
        handle.phase("写入清单")

    job = jobs.start_job("node_sync", "节点同步", 1, runner)
    done = await jobs.wait_job(job["id"])
    assert seen == ["上传节点"]
    assert done["phase"] == "已完成"


async def test_job_error_on_exception():
    async def runner(handle):
        handle.advance({"name": "a", "ok": True})
        raise RuntimeError("云端清单读取失败")

    job = jobs.start_job("workflow_sync", "x", 1, runner)
    done = await jobs.wait_job(job["id"])
    assert done["state"] == "error"
    assert "云端清单读取失败" in (done["error"] or "")


async def test_job_error_when_all_items_fail():
    async def runner(handle):
        handle.advance({"name": "a", "ok": False, "error": "本地不存在"})

    job = jobs.start_job("node_sync", "x", 1, runner)
    done = await jobs.wait_job(job["id"])
    assert done["state"] == "error"
    assert "1 个条目失败" in (done["error"] or "")


async def test_job_error_when_no_items():
    async def runner(handle):
        handle.log("无事可做")

    job = jobs.start_job("node_sync", "x", 0, runner)
    done = await jobs.wait_job(job["id"])
    assert done["state"] == "error"
    assert "没有可执行的条目" in (done["error"] or "")


async def test_job_cooperative_cancel():
    async def runner(handle):
        handle.advance({"name": "a", "ok": True})
        handle.check_cancel()  # 第二个条目前停止
        handle.advance({"name": "b", "ok": True})

    job = jobs.start_job("node_sync", "x", 2, runner, cancellable=True)
    assert jobs.request_cancel(job["id"]) is True
    done = await jobs.wait_job(job["id"])
    assert done["state"] == "cancelled"
    assert [i["name"] for i in done["items"]] == ["a"]  # 已完成条目保留


async def test_request_cancel_guards():
    async def runner(handle):
        await asyncio.sleep(0)

    job = jobs.start_job("node_sync", "x", 1, runner, cancellable=False)
    assert jobs.request_cancel(job["id"]) is False  # cancellable=False 不可取消
    assert jobs.request_cancel("nope") is False  # 不存在的作业
    await jobs.wait_job(job["id"])


async def _noop(handle):
    pass


def test_prune_keeps_running_and_newest(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_JOBS", 3)

    async def scenario():
        for i in range(5):
            job = jobs.start_job("node_sync", f"j{i}", 1, _noop)
            await jobs.wait_job(job["id"])

    asyncio.run(scenario())
    rows = jobs.list_jobs(limit=10)
    assert len(rows) <= 3
    # 最新的作业一定还在
    assert any(r["label"] == "j4" for r in rows)


# ---------------- API 面 ----------------


async def test_jobs_api_list_detail_cancel():
    async def runner(handle):
        handle.advance({"name": "a", "ok": True})

    job = jobs.start_job("workflow_sync", "wf（1 项）", 1, runner)
    await jobs.wait_job(job["id"])

    listed = await jobs_api.list_jobs(limit=10)
    assert listed["running"] == 0 and listed["jobs"][0]["id"] == job["id"]
    detail = await jobs_api.job_detail(job["id"])
    assert detail["job"]["items"][0]["name"] == "a"
    with pytest.raises(HTTPException):
        await jobs_api.job_detail("missing")
    cancel = await jobs_api.cancel_job(job["id"])
    assert cancel["cancelled"] is False  # 已结束的作业不可再取消
    with pytest.raises(HTTPException):
        await jobs_api.cancel_job("missing")


async def test_interrupted_job_is_listed_and_detail_shows_retry_guidance():
    from app.core import job_store
    from app.events import publish

    job_store.default_store().create("sync", "restart-job", {
        "id": "restart-job",
        "kind": "workflow_sync",
        "label": "重启前工作流同步",
        "state": "interrupted",
        "phase": "已中断（重启恢复）",
        "total": 2,
        "done": 1,
        "items": [{"name": "ok.json", "ok": True, "action": "push"}],
        "logs": [],
        "error": "桥接器重启，作业已中断",
        "cancellable": True,
        "created_ms": 1,
        "started_ms": 1,
        "finished_ms": 2,
    })
    listed = await jobs_api.list_jobs(limit=10)
    assert listed["running"] == 0
    assert listed["jobs"][0]["state"] == "interrupted"
    detail = await jobs_api.job_detail("restart-job")
    assert detail["job"]["items"][0] == {"name": "ok.json", "ok": True, "action": "push"}
    assert detail["job"]["items"][1]["ok"] is False
    assert "未完成条目（1 项）" == detail["job"]["items"][1]["name"]
    assert publish("job", {"action": "test", "job": detail["job"]})["event"] == "job"


# ---------------- 端点级：工作流同步任务化（复用假卷，见 test_workflows_sync） ----------------


async def test_nodes_sync_endpoint_returns_job(monkeypatch, workspace_tmp_path):
    from app.api.v1 import nodes as nodes_api
    from app.nodes_mgr import diff_cache

    tmp_path = workspace_tmp_path
    monkeypatch.setattr(diff_cache, "_path", lambda: tmp_path / "cache.json")
    diff_cache._doc = None
    monkeypatch.setattr(nodes_api, "custom_nodes_dir", lambda: tmp_path)
    pkg = tmp_path / "p1"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    staged_pkg = tmp_path / "staged" / "p1"
    staged_pkg.mkdir(parents=True)
    monkeypatch.setattr(nodes_api, "_stage_dir", lambda _src: staged_pkg)
    monkeypatch.setattr(
        nodes_api, "scan_custom_nodes",
        lambda: {"path": str(tmp_path),
                 "packages": [{"name": "p1", "repo": None, "commit": None,
                               "has_requirements": False, "enabled": True}],
                 "single_files": []},
    )
    puts: list[tuple[str, str]] = []
    dir_puts: list[tuple[str, str]] = []

    async def fake_put(local_path, remote, **kwargs):
        puts.append((str(local_path), remote))

    async def fake_put_dir(local_path, remote, **kwargs):
        dir_puts.append((str(local_path), remote))

    async def fake_read_manifest():
        return None

    async def fake_write_manifest(manifest):
        pass

    monkeypatch.setattr(nodes_api.volume_cli, "put_file", fake_put)
    monkeypatch.setattr(nodes_api.volume_cli, "put_directory", fake_put_dir)
    monkeypatch.setattr(nodes_api, "_read_cloud_manifest", fake_read_manifest)
    monkeypatch.setattr(nodes_api, "_write_cloud_manifest", fake_write_manifest)

    res = await nodes_api.nodes_sync(nodes_api.NamesBody(names=["p1"]))
    assert res["ok"] is True and res["job"]["state"] == "running"
    assert res["job"]["total"] == 1

    job = await nodes_api.jobs.wait_job(res["job"]["id"])
    assert job["state"] == "ok", {
        "error": job.get("error"),
        "logs": job.get("logs"),
        "items": job.get("items"),
    }
    assert job["items"][0]["ok"] is True
    assert not puts
    assert len(dir_puts) == 1 and dir_puts[0][1] == "custom_nodes/p1"

    empty = await nodes_api.nodes_sync(nodes_api.NamesBody(names=[]))
    assert empty["ok"] is False and empty["job"] is None

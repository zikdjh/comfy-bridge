"""M2-1 余项单测：节点 diff 持久化缓存（矩阵 TTL + deep class_type 持久块）与 Manager API 兜底采集。

端点级测试全部假 IO（扫描/卷清单/object_info），不打真实 Modal 与本地 ComfyUI。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.api.v1 import nodes as nodes_api
from app.nodes_mgr import diff_cache, manager_api


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "nodes_diff_cache.json"
    monkeypatch.setattr(diff_cache, "_path", lambda: path)
    diff_cache._doc = None
    yield path
    diff_cache._doc = None


def _payload(ok: bool = True) -> dict:
    return {
        "ok": ok,
        "cloud_error": None if ok else "boom",
        "app_state": "running",
        "volume": "vol-x",
        "manifest": {"local": {}, "cloud": {}},
        "rows": [{"name": "pkg_a", "state": "same"}],
        "counts": {"synced": 1, "only_local": 0, "only_cloud": 0},
    }


# ---------------- diff_cache：矩阵区块 ----------------


def test_matrix_roundtrip(cache_file):
    payload = _payload()
    diff_cache.save_matrix(payload)
    hit = diff_cache.load_matrix()
    assert hit is not None
    assert hit["cached"] is True and hit["cached_at"]
    assert hit["rows"] == payload["rows"]
    assert "app_state" not in hit  # 运行时状态不进缓存
    assert "cached" not in payload  # 原响应对象不被污染


def test_matrix_ttl_expiry(cache_file):
    diff_cache.save_matrix(_payload())
    diff_cache._doc["matrix_ms"] = (time.time() - 200) * 1000  # 超过 120s TTL
    assert diff_cache.load_matrix() is None
    assert diff_cache.load_matrix(max_age_ms=1e9) is not None  # 调宽 TTL 又可见


def test_matrix_failure_not_cached(cache_file):
    diff_cache.save_matrix(_payload(ok=False))
    assert diff_cache.load_matrix() is None
    assert not cache_file.exists()  # 失败结果不落盘


# ---------------- diff_cache：deep class_type 持久块 ----------------


def test_class_types_roundtrip(cache_file):
    diff_cache.save_class_types({"local": ["KSampler"], "cloud": ["KSampler", "CloudOnly"], "error": None})
    hit = diff_cache.load_class_types()
    assert hit is not None
    assert hit["local"] == ["KSampler"]
    assert hit["cloud"] == ["KSampler", "CloudOnly"]
    assert hit["cached"] is True and hit["cached_at"]


def test_invalidate_keeps_class_types(cache_file):
    diff_cache.save_matrix(_payload())
    diff_cache.save_class_types({"local": ["a"], "cloud": None, "error": None})
    diff_cache.invalidate_matrix()
    assert diff_cache.load_matrix() is None  # 矩阵失效
    assert diff_cache.load_class_types() is not None  # deep 块与卷内容无关，保留


# ---------------- Manager API 兜底：解析与增强（纯函数） ----------------


def test_parse_getlist_shapes():
    doc = {
        "custom_nodes": [
            {"cnr_id": "foo-bar", "title": "Foo Bar", "ver": "1.2.3",
             "files": ["https://github.com/x/foo-bar"]},
            {"title": "My Pack", "reference": "https://github.com/y/my-pack"},
            "not-a-dict",
        ]
    }
    idx = manager_api.parse_getlist(doc)
    assert idx["foobar"]["repo"] == "https://github.com/x/foo-bar"
    assert idx["foobar"]["version"] == "1.2.3"
    assert idx["mypack"]["repo"] == "https://github.com/y/my-pack"
    # 顶层直接是列表 / 完全不识别的形态
    assert manager_api.parse_getlist([{"title": "T"}])["t"]["title"] == "T"
    assert manager_api.parse_getlist(None) == {}
    assert manager_api.parse_getlist({"custom_nodes": "x"}) == {}


def test_apply_enrichment():
    scan = {
        "packages": [
            {"name": "my_pack", "repo": None, "commit": None, "has_requirements": False, "enabled": True},
            {"name": "git_pkg", "repo": "https://github.com/z/git-pkg", "commit": "abc",
             "has_requirements": True, "enabled": True},
        ],
        "single_files": [],
    }
    index = {
        "mypack": {"repo": "https://github.com/y/my-pack", "title": "My Pack", "version": "2.0"},
        "gitpkg": {"repo": "https://github.com/other/git-pkg", "title": "Git Pkg", "version": "0.1"},
    }
    matched = manager_api.apply_enrichment(scan, index)
    assert matched == 2
    p = scan["packages"][0]
    assert p["repo"] == "https://github.com/y/my-pack"  # 非 git 包补齐 repo
    assert p["repo_via_manager"] is True
    assert p["manager_version"] == "2.0"
    g = scan["packages"][1]
    assert g["repo"] == "https://github.com/z/git-pkg"  # git 包已有 repo 不覆盖
    assert g["manager_version"] == "0.1"
    assert "repo_via_manager" not in g


# ---------------- Manager API 兜底：fetch（假传输，含缓存） ----------------


def _fake_settings(url: str):
    return SimpleNamespace(local=SimpleNamespace(comfy_url=url))


@pytest.mark.parametrize("mode", ["unconfigured", "offline"])
async def test_fetch_index_degrades(mode, monkeypatch):
    monkeypatch.setattr(manager_api, "_cache", None)
    monkeypatch.setattr(
        manager_api, "get_settings",
        lambda: _fake_settings("" if mode == "unconfigured" else "http://127.0.0.1:9"),
    )

    async def boom(base, path):
        raise ConnectionError("refused")

    monkeypatch.setattr(manager_api, "_get_json", boom)
    result = await manager_api.fetch_index()
    assert result["online"] is False
    assert result["index"] is None and result["entries"] == 0
    assert result["error"]


async def test_fetch_index_success_and_cache(monkeypatch):
    monkeypatch.setattr(manager_api, "_cache", None)
    monkeypatch.setattr(manager_api, "get_settings", lambda: _fake_settings("http://mock:1"))
    calls = {"n": 0}

    async def fake_get(base, path):
        calls["n"] += 1
        assert path == "/customnode/getlist"
        return {"custom_nodes": [{"cnr_id": "foo-bar"}]}

    monkeypatch.setattr(manager_api, "_get_json", fake_get)
    r1 = await manager_api.fetch_index()
    assert r1["online"] is True and r1["entries"] == 1
    r2 = await manager_api.fetch_index()  # 命中缓存不再发请求
    assert calls["n"] == 1 and r2["entries"] == 1


# ---------------- 端点级：diff 缓存行为（假 IO） ----------------


@pytest.fixture
def diff_env(tmp_path, monkeypatch):
    monkeypatch.setattr(diff_cache, "_path", lambda: tmp_path / "nodes_diff_cache.json")
    diff_cache._doc = None
    scan = {
        "path": "/x",
        "packages": [{"name": "pkg_a", "repo": "https://r/a", "commit": "c1",
                      "has_requirements": True, "enabled": True}],
        "single_files": [],
    }
    monkeypatch.setattr(nodes_api, "scan_custom_nodes", lambda: scan)

    async def fake_cloud():
        return {"ok": True, "error": None, "volume": "vol-x", "app_state": "running",
                "packages": [{"name": "pkg_a", "size": "1 MB"}], "single_files": [], "manifest": None}

    monkeypatch.setattr(nodes_api, "_cloud_listing", fake_cloud)

    async def fake_local_ct():
        return ["KSampler", "SaveImage"]

    monkeypatch.setattr(nodes_api, "_local_class_types", fake_local_ct)

    calls = {"cloud_obj": 0, "ensure": 0}

    async def fake_fetch_obj():
        calls["cloud_obj"] += 1
        return {"KSampler": {}, "SaveImage": {}, "CloudOnly": {}}

    async def fake_ensure():
        calls["ensure"] += 1
        return "running-live"

    monkeypatch.setattr(nodes_api.runner_client, "fetch_object_info", fake_fetch_obj)
    monkeypatch.setattr(nodes_api.runner_client, "ensure_state", fake_ensure)
    return calls


async def test_diff_matrix_cache_hit(diff_env):
    r1 = await nodes_api.nodes_diff(deep=False, force=False)
    assert r1["cached"] is False and r1["cached_at"] is None
    assert r1["app_state"] == "running"
    r2 = await nodes_api.nodes_diff(deep=False, force=False)
    assert r2["cached"] is True and r2["cached_at"]
    assert r2["rows"] == r1["rows"]
    assert r2["app_state"] == "running-live"  # 缓存命中时 app_state 现取
    assert diff_env["ensure"] == 1


async def test_diff_force_bypass(diff_env):
    await nodes_api.nodes_diff(deep=False, force=False)
    r = await nodes_api.nodes_diff(force=True)
    assert r["cached"] is False


async def test_diff_failure_not_cached(diff_env, monkeypatch):
    async def bad_cloud():
        return {"ok": False, "error": "boom", "volume": "vol-x", "app_state": None,
                "packages": [], "single_files": [], "manifest": None}

    monkeypatch.setattr(nodes_api, "_cloud_listing", bad_cloud)
    r1 = await nodes_api.nodes_diff()
    r2 = await nodes_api.nodes_diff(deep=False, force=False)
    assert r1["cached"] is False and r2["cached"] is False
    assert r2["cloud_error"] == "boom"


async def test_deep_class_types_persist(diff_env):
    r1 = await nodes_api.nodes_diff(deep=True)
    assert r1["class_types"]["cached"] is False
    assert r1["class_types"]["cloud"] == ["CloudOnly", "KSampler", "SaveImage"]
    assert diff_env["cloud_obj"] == 1
    r2 = await nodes_api.nodes_diff(deep=True, force=False)  # 再次进页面：缓存命中，不重拉云端
    assert r2["class_types"]["cached"] is True and r2["class_types"]["cached_at"]
    assert diff_env["cloud_obj"] == 1
    r3 = await nodes_api.nodes_diff(deep=True, force=True)  # 强制刷新才重拉
    assert r3["class_types"]["cached"] is False
    assert diff_env["cloud_obj"] == 2


# ---------------- 端点级：同步/移除使矩阵缓存失效 ----------------


async def test_sync_invalidates_matrix(diff_env, tmp_path, monkeypatch):
    await nodes_api.nodes_diff(deep=False, force=False)
    assert diff_cache.load_matrix() is not None

    pkg_dir = tmp_path / "new_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    monkeypatch.setattr(nodes_api, "custom_nodes_dir", lambda: tmp_path)
    monkeypatch.setattr(
        nodes_api, "scan_custom_nodes",
        lambda: {"path": str(tmp_path),
                 "packages": [{"name": "new_pkg", "repo": None, "commit": None,
                               "has_requirements": False, "enabled": True}],
                 "single_files": []},
    )
    puts: list[str] = []

    async def fake_put(local_path, remote):
        puts.append(remote)

    async def fake_read_manifest():
        return None

    writes: list[dict] = []

    async def fake_write_manifest(manifest):
        writes.append(manifest)

    monkeypatch.setattr(nodes_api, "_volume_put", fake_put)
    monkeypatch.setattr(nodes_api, "_read_cloud_manifest", fake_read_manifest)
    monkeypatch.setattr(nodes_api, "_write_cloud_manifest", fake_write_manifest)

    res = await nodes_api.nodes_sync(nodes_api.NamesBody(names=["new_pkg"]))
    job = await nodes_api.jobs.wait_job(res["job"]["id"])
    assert job["state"] == "ok" and job["items"][0]["ok"] is True
    assert puts == ["custom_nodes/new_pkg"]
    assert writes and writes[0]["schema"] == 1
    assert diff_cache.load_matrix() is None  # 缓存已失效


async def test_cloud_remove_invalidates_matrix(diff_env, monkeypatch):
    await nodes_api.nodes_diff(deep=False, force=False)
    assert diff_cache.load_matrix() is not None

    removed: list[str] = []

    async def fake_remove(remote):
        removed.append(remote)

    monkeypatch.setattr(nodes_api, "_remove_remote", fake_remove)
    res = await nodes_api.nodes_cloud_remove(nodes_api.NamesBody(names=["pkg_a"]))
    assert res["ok"] is True
    assert "custom_nodes/pkg_a" in removed
    assert diff_cache.load_matrix() is None


# ---------------- 端点级：/nodes/local Manager 增强 ----------------


async def test_nodes_local_manager_enrichment(monkeypatch):
    async def fake_obj():
        return {"KSampler": {}}

    async def fake_stats():
        return {"system": {"comfyui_version": "0.3.0"}}

    monkeypatch.setattr(nodes_api.comfy_client, "fetch_object_info", fake_obj)
    monkeypatch.setattr(nodes_api.comfy_client, "fetch_system_stats", fake_stats)
    monkeypatch.setattr(
        nodes_api, "scan_custom_nodes",
        lambda: {"path": "/x",
                 "packages": [{"name": "comfybridge-openai-image", "repo": None, "commit": None,
                               "has_requirements": False, "enabled": True}],
                 "single_files": []},
    )

    async def fake_fetch_index():
        return {"online": True, "entries": 5, "error": None,
                "index": {"comfybridgeopenaiimage": {"repo": "https://github.com/a/b",
                                                     "title": "CB OpenAI Image", "version": "1.1"}}}

    monkeypatch.setattr(nodes_api.manager_api, "fetch_index", fake_fetch_index)
    res = await nodes_api.nodes_local()
    assert res["manager"] == {"online": True, "entries": 5, "error": None, "matched": 1}
    pkg = res["custom_nodes"]["packages"][0]
    assert pkg["repo"] == "https://github.com/a/b"
    assert pkg["repo_via_manager"] is True
    assert pkg["manager_version"] == "1.1"
    assert pkg["manager_title"] == "CB OpenAI Image"


async def test_nodes_local_manager_offline_silent(monkeypatch):
    """本机未装 Manager / ComfyUI 离线：主清单不受影响，manager.online=false。"""
    async def fake_stats():
        return {"system": {"comfyui_version": "0.3.0"}}

    monkeypatch.setattr(nodes_api.comfy_client, "fetch_object_info", fake_stats)
    monkeypatch.setattr(
        nodes_api, "scan_custom_nodes",
        lambda: {"path": "/x",
                 "packages": [{"name": "p", "repo": None, "commit": None,
                               "has_requirements": False, "enabled": True}],
                 "single_files": []},
    )

    async def fake_fetch_index():
        return {"online": False, "entries": 0, "error": "HTTP 404", "index": None}

    monkeypatch.setattr(nodes_api.manager_api, "fetch_index", fake_fetch_index)
    res = await nodes_api.nodes_local()
    assert res["manager"] == {"online": False, "entries": 0, "error": "HTTP 404"}
    pkg = res["custom_nodes"]["packages"][0]
    assert pkg["repo"] is None and "repo_via_manager" not in pkg

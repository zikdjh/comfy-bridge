"""gui_static 挂载与 SPA fallback（M4 打包分发）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gui_static import mount_gui


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_no_artifact_skips_mount(tmp_path: Path) -> None:
    app = FastAPI()
    assert mount_gui(app, tmp_path) is False
    client = TestClient(app)
    assert client.get("/bridge").status_code == 404


def _mounted_app(root: Path) -> TestClient:
    _write_tree(root, {
        "index.html": "<html>bridge-index</html>",
        "assets/app.js": "console.log(1)",
        "vite.svg": "<svg/>",
    })
    app = FastAPI()
    assert mount_gui(app, root) is True
    return TestClient(app)


def test_spa_routes_fall_back_to_index(tmp_path: Path) -> None:
    client = _mounted_app(tmp_path)
    for path in ("/bridge", "/bridge/", "/bridge/settings", "/bridge/nodes/deep"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "bridge-index" in resp.text, path


def test_real_files_served_from_root(tmp_path: Path) -> None:
    client = _mounted_app(tmp_path)
    assert client.get("/bridge/vite.svg").text == "<svg/>"


def test_assets_mount_missing_file_is_404(tmp_path: Path) -> None:
    # 资产缺失必须 404 暴露问题，而不是回落 index.html 掩盖
    client = _mounted_app(tmp_path)
    assert client.get("/bridge/assets/missing.js").status_code == 404
    assert client.get("/bridge/assets/app.js").text == "console.log(1)"


def test_path_traversal_stays_inside_artifacts(tmp_path: Path) -> None:
    _write_tree(tmp_path / "outside", {"secret.txt": "secret"})
    root = tmp_path / "static"
    client = _mounted_app(root)
    resp = client.get("/bridge/%2e%2e/outside/secret.txt")
    assert resp.status_code == 200
    assert "secret" not in resp.text
    assert "bridge-index" in resp.text

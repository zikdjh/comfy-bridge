"""nodes.lock.yaml 生成/渲染与回滚行为（M2-2 镜像固化）。"""

import json

import pytest

from app.nodes_mgr import lockfile, version_align


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    """锁/脚本与版本 pin 都指到临时目录。"""
    monkeypatch.setattr(lockfile, "LOCK_FILE", tmp_path / "lock" / "nodes.lock.yaml")
    monkeypatch.setattr(lockfile, "SCRIPT_FILE", tmp_path / "lock" / "install_nodes.sh")
    monkeypatch.setattr(version_align, "LOCK_FILE", tmp_path / "lock" / "comfyui_version.txt")
    return tmp_path


def _scan(path, packages):
    return {"path": str(path), "packages": packages, "single_files": []}


def _make_node(root, name, reqs, has_reqs=True):
    """落盘一个节点包目录（generate 从磁盘读 requirements.txt）。"""
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    if has_reqs:
        (pkg / "requirements.txt").write_text(reqs, encoding="utf-8")


def test_parse_requirements_strips_comments_and_blanks():
    text = (
        "# full line comment\n"
        "\n"
        "requests>=2.0\n"
        "pillow  # inline comment\n"
        "torch>=2.0; platform_system == 'Linux'\n"
        "git+https://example.test/repo#egg=frag\n"
    )
    assert lockfile.parse_requirements(text) == [
        "requests>=2.0",
        "pillow",
        "torch>=2.0; platform_system == 'Linux'",
        "git+https://example.test/repo#egg=frag",
    ]


def test_generate_includes_enabled_packages_only(tmp_path, lock_env):
    root = tmp_path / "custom_nodes"
    root.mkdir()
    _make_node(root, "GoodNode", "requests\n# c\n\npillow\n")
    _make_node(root, "DisabledNode.disabled", "requests\n")
    _make_node(root, "NoReqNode", "", has_reqs=False)

    scan = _scan(root, [
        {"name": "GoodNode", "repo": "https://x/y.git", "commit": "abc1234",
         "has_requirements": True, "enabled": True},
        {"name": "DisabledNode.disabled", "repo": None, "commit": None,
         "has_requirements": True, "enabled": False},
        {"name": "NoReqNode", "repo": None, "commit": None,
         "has_requirements": False, "enabled": True},
    ])
    lock = lockfile.generate(scan)
    assert [n["name"] for n in lock["nodes"]] == ["GoodNode"]
    assert lock["nodes"][0]["requirements"] == ["requests", "pillow"]
    assert lock["nodes"][0]["commit"] == "abc1234"
    assert lock["comfyui_version"] == version_align.DEFAULT_PIN
    assert lock["schema"] == 1


def test_render_install_script_installs_each_requirement(tmp_path, lock_env):
    lock = {"schema": 1, "comfyui_version": "v0.38.0", "nodes": [
        {"name": "A", "requirements": ["requests>=2.0", "pillow"]},
        {"name": "B", "requirements": []},
    ]}
    script = lockfile.render_install_script(lock)
    assert script.startswith("#!/usr/bin/env bash")
    assert "set -e" in script
    assert '"requests>=2.0"' in script and '"pillow"' in script
    assert script.count("pip install") == 1  # 无依赖的 B 不产生安装行


def test_render_install_script_empty_lock(tmp_path, lock_env):
    script = lockfile.render_install_script({"schema": 1, "nodes": []})
    assert "无需预装" in script


def test_lock_file_is_valid_yaml_json(tmp_path, lock_env):
    lock = {"schema": 1, "comfyui_version": "v0.38.0", "nodes": [
        {"name": "A", "requirements": ["x"]}
    ]}
    lockfile.write_outputs(lock, "#!/bin/bash\n")
    data = json.loads(lockfile.LOCK_FILE.read_text(encoding="utf-8"))
    assert data["nodes"][0]["name"] == "A"


def test_backup_and_restore_roundtrip(tmp_path, lock_env):
    lockfile.write_outputs(
        {"schema": 1, "nodes": [{"name": "old", "requirements": []}]}, "old-script"
    )
    backup = lockfile.backup_current()
    lockfile.write_outputs({"schema": 1, "nodes": [{"name": "new", "requirements": []}]}, "new-script")
    assert "new" in lockfile.LOCK_FILE.read_text(encoding="utf-8")

    lockfile.restore(*backup)
    assert "old" in lockfile.LOCK_FILE.read_text(encoding="utf-8")
    assert "old-script" in lockfile.SCRIPT_FILE.read_text(encoding="utf-8")


def test_restore_removes_newly_created_files(tmp_path, lock_env):
    backup = lockfile.backup_current()  # 此前不存在
    assert backup == (None, None)
    lockfile.write_outputs({"schema": 1, "nodes": []}, "x")
    assert lockfile.LOCK_FILE.is_file() and lockfile.SCRIPT_FILE.is_file()

    lockfile.restore(*backup)
    assert not lockfile.LOCK_FILE.exists()
    assert not lockfile.SCRIPT_FILE.exists()

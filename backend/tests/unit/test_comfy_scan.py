"""ComfyUI 根目录完整性校验（inspect_root）单元测试：合成目录，不碰真实磁盘安装。"""

from pathlib import Path

from app.core import comfy_scan

_REQUIRED = [label for _, label in comfy_scan._REQUIRED]
_OPTIONAL = [label for _, label in comfy_scan._OPTIONAL]


def _make_root(base: Path, *, required: bool = True, optional: bool = False, version: str | None = "0.3.75") -> Path:
    root = base / "ComfyUI"
    root.mkdir(parents=True)
    if required:
        for rel in ("main.py", "nodes.py", "execution.py", "folder_paths.py"):
            (root / rel).write_text("# stub\n", encoding="utf-8")
        (root / "comfy").mkdir()
        (root / "comfy_extras").mkdir()
    if optional:
        for rel in ("models", "custom_nodes", "input", "output", "user"):
            (root / rel).mkdir()
        (root / "requirements.txt").write_text("torch\n", encoding="utf-8")
        if version is not None:
            (root / "comfyui_version.py").write_text(
                f'__version__ = "{version}"\n', encoding="utf-8"
            )
    return root


def test_valid_complete_root(tmp_path):
    root = _make_root(tmp_path, optional=True)
    report = comfy_scan.inspect_root(str(root))
    assert report["exists"] is True
    assert report["valid"] is True
    assert report["required_missing"] == []
    assert report["optional_missing"] == []
    assert report["version"] == "0.3.75"
    assert report["has_venv"] is False


def test_required_only_root_is_valid_with_optional_missing(tmp_path):
    root = _make_root(tmp_path)
    report = comfy_scan.inspect_root(str(root))
    assert report["valid"] is True
    assert sorted(report["optional_missing"]) == sorted(_OPTIONAL)


def test_empty_dir_missing_all_required(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    report = comfy_scan.inspect_root(str(root))
    assert report["exists"] is True
    assert report["valid"] is False
    assert sorted(report["required_missing"]) == sorted(_REQUIRED)


def test_nonexistent_path(tmp_path):
    report = comfy_scan.inspect_root(str(tmp_path / "nope"))
    assert report["exists"] is False
    assert report["valid"] is False
    assert report["required_missing"] == []
    assert report["optional_missing"] == []


def test_missing_single_required_invalidates(tmp_path):
    root = _make_root(tmp_path, optional=True)
    (root / "execution.py").unlink()
    report = comfy_scan.inspect_root(str(root))
    assert report["valid"] is False
    assert report["required_missing"] == ["execution.py"]
    assert report["optional_missing"] == []


def test_venv_detection(tmp_path):
    root = _make_root(tmp_path)
    py = root / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"stub")
    report = comfy_scan.inspect_root(str(root))
    assert report["has_venv"] is True

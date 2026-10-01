"""本地 custom_nodes/ 扫描（从 api/v1/nodes 抽出，供镜像固化锁文件复用）。

目录型节点包取 git remote+commit、requirements.txt 与启停状态；单文件节点单独登记。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.config import custom_nodes_dir

DISABLED_SUFFIX = ".disabled"  # ComfyUI-Manager 停用约定：目录名追加 .disabled


def _git(dir_path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(dir_path), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _pkg_git_info(entry: Path) -> tuple[str | None, str | None]:
    """包目录自身的 git 信息；非独立仓库（会向上越界到 ComfyUI 主仓库）返回 (None, None)。"""
    top = _git(entry, "rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != entry.resolve():
        return None, None
    return _git(entry, "remote", "get-url", "origin"), _git(entry, "rev-parse", "--short", "HEAD")


def scan_custom_nodes() -> dict:
    """扫描 custom_nodes/：目录型节点包（git 信息）+ 单文件节点。"""
    root = custom_nodes_dir()
    packages: list[dict] = []
    single_files: list[str] = []
    if not root.is_dir():
        return {"path": str(root), "packages": packages, "single_files": single_files}

    for entry in sorted(root.iterdir()):
        if entry.name.startswith("__") or entry.name.startswith("."):
            continue
        if entry.is_dir():
            if not (entry / "__init__.py").is_file():
                continue
            repo, commit = _pkg_git_info(entry)
            packages.append(
                {
                    "name": entry.name,
                    "repo": repo,
                    "commit": commit,
                    "has_requirements": (entry / "requirements.txt").is_file(),
                    "enabled": not entry.name.endswith(DISABLED_SUFFIX),
                }
            )
        elif entry.suffix == ".py":
            single_files.append(entry.name)
    return {"path": str(root), "packages": packages, "single_files": single_files}

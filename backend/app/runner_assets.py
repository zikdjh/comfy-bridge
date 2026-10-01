"""modal-runner 资源定位：仓库布局优先，安装布局（pipx / pip wheel）回落到包内副本。

仓库开发时 modal-runner/ 在仓库根（backend/app 向上两级）；wheel 安装后该目录
不存在，改用打包时嵌入的 app/_modal_runner/ 副本（scripts/build_package.py 复制）。
各模块的 runner 文件常量统一经此解析，禁止再手写 parents[N] 相对路径。
"""

from __future__ import annotations

from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _APP_DIR.parents[1]
_PACKAGED_DIR = _APP_DIR / "_modal_runner"


def runner_dir() -> Path:
    """modal-runner 目录：仓库副本存在即用（开发态），否则用包内嵌入副本。"""
    repo = _REPO_ROOT / "modal-runner"
    if (repo / "runner.py").is_file():
        return repo
    return _PACKAGED_DIR


def runner_file(*parts: str) -> Path:
    return runner_dir().joinpath(*parts)

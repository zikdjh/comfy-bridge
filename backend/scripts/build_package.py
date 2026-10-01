"""一键打包 wheel（M4 分发）：前端嵌入构建 → modal-runner 副本 → uv build → 产物自检。

用法：cd backend && uv run python scripts/build_package.py [--no-frontend]
产物：dist/comfy_bridge-*.whl（pipx install / pip install 直接可用）。
自检：wheel 内必须含 app/static/index.html 与 app/_modal_runner/runner.py，缺失即失败。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND = REPO / "frontend"
STATIC = BACKEND / "app" / "static"
PKG_ASSETS = BACKEND / "pkg_assets"


def run(cmd: list[str], **kw: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def build_frontend() -> None:
    pnpm = shutil.which("pnpm")
    if not pnpm:
        sys.exit("未找到 pnpm；或先手动 pnpm build:embed 再用 --no-frontend 跳过")
    run([pnpm, "build:embed"], cwd=FRONTEND)
    if not (STATIC / "index.html").is_file():
        sys.exit("前端产物缺失 app/static/index.html")


def copy_runner() -> None:
    """modal-runner 在工程根之外，hatchling force-include 只收工程内路径，先复制进来。"""
    src = REPO / "modal-runner"
    dst = PKG_ASSETS / "modal-runner"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"modal-runner 已复制 → {dst.relative_to(REPO)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="打包 comfy-bridge wheel")
    parser.add_argument("--no-frontend", action="store_true", help="跳过前端构建（复用现有 app/static）")
    args = parser.parse_args()

    if args.no_frontend:
        if not (STATIC / "index.html").is_file():
            sys.exit("app/static/index.html 不存在，先不带 --no-frontend 执行")
    else:
        build_frontend()
    copy_runner()

    uv = shutil.which("uv")
    if not uv:
        sys.exit("未找到 uv（打包用 uv build）")
    run([uv, "build"], cwd=BACKEND)

    wheels = sorted((BACKEND / "dist").glob("comfy_bridge-*.whl"))
    if not wheels:
        sys.exit("uv build 未产出 wheel")
    wheel = wheels[-1]
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    must = [
        "app/static/index.html",
        "app/_modal_runner/runner.py",
        "app/gui_static.py",
        "app/runner_assets.py",
        "app/cli.py",
    ]
    missing = [m for m in must if m not in names]
    if missing:
        sys.exit(f"wheel 缺少关键文件: {missing}")
    runner_files = sum(1 for n in names if n.startswith("app/_modal_runner/"))
    static_files = sum(1 for n in names if n.startswith("app/static/"))
    print(f"OK {wheel.name}  共 {len(names)} 文件（GUI 产物 {static_files}，modal-runner 副本 {runner_files}）")


if __name__ == "__main__":
    main()

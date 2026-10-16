"""构建 ComfyBridge 整合包（zip，解压即用）。

包内组成：exe 启动器 + 裁剪版 CPython 运行时 + 运行依赖（取自 backend/.venv，
与测试环境同版本）+ 应用本体（wheel 内容：app 代码、GUI 产物、modal-runner 副本）
+ 使用说明。整合包不含 ComfyUI 本体；数据面由用户已有的本地 ComfyUI / Modal 云端提供。

用法：cd backend && python scripts/build_rust_package.py [--no-frontend] [--skip-wheel]
产物：backend/dist/ComfyBridge-<版本>-win64.zip
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
STATIC = BACKEND / "app" / "static"
VENV_SITE = BACKEND / ".venv" / "Lib" / "site-packages"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_package import build_frontend, copy_runner, run

# 标准库裁剪：仅去掉 GUI 工具 / 安装器 / 测试套件等运行时用不到的大块
LIB_PRUNE = {
    "__pycache__",
    "test",
    "idlelib",
    "turtledemo",
    "tkinter",
    "ensurepip",
    "venv",
    "site-packages",
}
# venv site-packages 只剔除开发工具链（前缀匹配），运行依赖整体保留
SITE_DEV_PREFIXES = (
    "pip",
    "pytest",
    "_pytest",
    "mypy",
    "mypyc",
    "ruff",
    "respx",
    "build",
    "pyproject_hooks",
    "hatchling",
    "pathspec",
    "trove_classifiers",
    "tomlkit",
    "iniconfig",
    "pluggy",
    "types_",
    "certifi-stubs",
    "toml-stubs",
)


def latest_wheel() -> Path:
    wheels = sorted((BACKEND / "dist").glob("comfy_bridge-*.whl"))
    if not wheels:
        sys.exit("backend/dist 未找到 comfy_bridge-*.whl；先执行 scripts/build_package.py")
    return wheels[-1]


def wheel_version(name: str) -> str:
    parts = name.removesuffix(".whl").split("-")
    if len(parts) < 5 or not parts[1]:
        sys.exit(f"无法从 wheel 文件名解析版本: {name}")
    return parts[1]


def runtime_base() -> Path:
    """内置运行时的 CPython 基目录：CB_BUNDLE_PYTHON 优先，其次当前解释器的 base_prefix。"""
    custom = os.environ.get("CB_BUNDLE_PYTHON")
    candidates = [Path(custom)] if custom else [Path(sys.base_prefix)]
    exe = "python.exe" if sys.platform == "win32" else "bin/python3"
    for candidate in candidates:
        if (candidate / exe).is_file():
            return candidate
    sys.exit(f"未找到 CPython 基目录（可用 CB_BUNDLE_PYTHON 指定）：{candidates}")


def lib_ignore(src_lib: Path):
    def _ignore(directory: str, names: list[str]) -> list[str]:
        skip: list[str] = []
        at_lib_root = Path(directory) == src_lib
        for name in names:
            if name == "__pycache__" or name.endswith(".pyc") or at_lib_root and name in LIB_PRUNE:
                skip.append(name)
        return skip

    return _ignore


def dev_dir_names(site: Path) -> list[str]:
    """枚举 venv site-packages 里的开发工具链目录/egg-info，作为 robocopy 精确排除名单。"""
    return [
        name
        for name in os.listdir(site)
        if name == "__pycache__" or name.startswith(SITE_DEV_PREFIXES)
    ]


def _robocopy(src: Path, dst: Path, *, dirs_exclude: list[str] | None = None,
              files_exclude: list[str] | None = None, files: list[str] | None = None) -> None:
    """Windows 装配走 robocopy：python.exe 写 vcruntime 等 DLL 会被 AV 行为拦截。"""
    cmd = ["robocopy", str(src), str(dst), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
    if files:
        cmd += files
    if dirs_exclude:
        cmd += ["/XD", *dirs_exclude]
    if files_exclude:
        cmd += ["/XF", *files_exclude]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # robocopy 返回码 0-7 均为成功（1=有文件复制，3=有复制有跳过…）
    if result.returncode > 7:
        sys.exit(f"robocopy 失败（{result.returncode}）:\n{result.stdout}\n{result.stderr}")


def assemble_runtime(base: Path, runtime: Path) -> None:
    runtime.mkdir(parents=True)
    if sys.platform == "win32":
        _robocopy_files(base, runtime, ["python.exe", "*.dll", "LICENSE.txt"])
        _robocopy(base / "DLLs", runtime / "DLLs")
        _robocopy(
            base / "Lib",
            runtime / "Lib",
            dirs_exclude=sorted(LIB_PRUNE | {"__pycache__"}),
            files_exclude=["*.pyc"],
        )
        return
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, runtime / dll.name)
    shutil.copy2(base / "python.exe", runtime / "python.exe")
    lic = base / "LICENSE.txt"
    if lic.is_file():
        shutil.copy2(lic, runtime / "LICENSE.txt")
    shutil.copytree(base / "DLLs", runtime / "DLLs")
    shutil.copytree(base / "Lib", runtime / "Lib", ignore=lib_ignore(base / "Lib"))


def _robocopy_files(src: Path, dst: Path, files: list[str]) -> None:
    cmd = ["robocopy", str(src), str(dst), *files, "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode > 7:
        sys.exit(f"robocopy 失败（{result.returncode}）:\n{result.stdout}\n{result.stderr}")


def site_ignore(_directory: str, names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name == "__pycache__" or name.endswith(".pyc") or name.startswith(SITE_DEV_PREFIXES)
    ]


def usage_text(version: str) -> str:
    return f"""ComfyBridge 整合包 {version}（解压即用，不需要安装）

1. 解压到任意目录（建议路径不含空格与中文）。
2. 双击 comfy-bridge.exe 启动；控制台默认 http://127.0.0.1:8189/bridge/。
3. 常用参数直接跟在 exe 后面：--host 127.0.0.1 --port 8189
4. 自检：comfy-bridge.exe --doctor    版本：comfy-bridge.exe --version

说明：
- 本整合包不含 ComfyUI 本体；出图数据面仍由你已有的本地 ComfyUI 或 Modal 云端提供。
- runtime/ 是内置 Python 运行环境，请勿单独改动；彻底卸载只需删除解压目录。
- ComfyUI 路径、Modal 凭证等配置存放在用户目录 ~/.comfybridge，与整合包目录无关。
"""


def smoke_check(python: Path) -> None:
    smoke = (
        "import fastapi, modal, uvicorn, httpx, websockets, pydantic; "
        "import app.cli, os; "
        "p = os.path.join(os.path.dirname(app.cli.__file__), 'static', 'index.html'); "
        "assert os.path.isfile(p), '缺少 GUI 产物: ' + p; "
        "print('bundle smoke ok')"
    )
    result = subprocess.run([str(python), "-c", smoke], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"整合包自检失败:\n{result.stdout}\n{result.stderr}")


def make_zip(bundle: Path, stage: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle))


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 ComfyBridge 整合包（zip，解压即用）")
    parser.add_argument("--no-frontend", action="store_true", help="跳过前端构建（复用现有 app/static）")
    parser.add_argument("--skip-wheel", action="store_true", help="跳过 uv build（复用现有 dist wheel）")
    args = parser.parse_args()

    if args.no_frontend:
        if not (STATIC / "index.html").is_file():
            sys.exit("app/static/index.html 不存在，先不带 --no-frontend 执行")
    else:
        build_frontend()
    if not args.skip_wheel:
        copy_runner()
        uv = shutil.which("uv")
        if not uv:
            sys.exit("未找到 uv（构建 wheel 用 uv build）")
        run([uv, "build"], cwd=BACKEND)

    wheel = latest_wheel()
    version = wheel_version(wheel.name)
    base = runtime_base()

    # staging 放系统临时目录：留在仓库内会被 pytest 递归收集第三方测试
    workdir = Path(tempfile.mkdtemp(prefix="cb-bundle-"))
    bundle = workdir
    stage = bundle / f"ComfyBridge-{version}"
    runtime = stage / "runtime"

    try:
        print(f"组装运行时: {base} -> {runtime}")
        assemble_runtime(base, runtime)

        if not VENV_SITE.is_dir():
            sys.exit(f"未找到 {VENV_SITE}；先执行 uv sync 初始化开发环境")
        site_dst = runtime / "Lib" / "site-packages"
        if sys.platform == "win32":
            _robocopy(
                VENV_SITE,
                site_dst,
                dirs_exclude=dev_dir_names(VENV_SITE),
                files_exclude=["*.pyc"],
            )
        else:
            shutil.copytree(VENV_SITE, site_dst, ignore=site_ignore)

        # wheel 内含 app 代码 + GUI 产物 + modal-runner 副本，直接解包进 site-packages
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(runtime / "Lib" / "site-packages")

        cargo = shutil.which("cargo")
        if not cargo:
            sys.exit("未找到 cargo（需要 Rust 工具链）")
        run([cargo, "build", "--release"], cwd=REPO / "packager")
        exe_name = "comfy-bridge.exe" if sys.platform == "win32" else "comfy-bridge"
        built = REPO / "packager" / "target" / "release" / exe_name
        if not built.is_file():
            sys.exit(f"cargo 产物缺失: {built}")
        shutil.copy2(built, stage / exe_name)

        (stage / "使用说明.txt").write_text(usage_text(version), encoding="utf-8-sig")

        python = runtime / "python.exe" if sys.platform == "win32" else runtime / "bin" / "python3"
        smoke_check(python)

        out = BACKEND / "dist" / f"ComfyBridge-{version}-win64.zip"
        make_zip(bundle, stage, out)
        print(f"OK {out.name}  ({out.stat().st_size / 1024 / 1024:.1f} MiB)  app={wheel.name}  python={base.name}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()

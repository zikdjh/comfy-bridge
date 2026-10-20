"""构建 ComfyBridge 桌面整合包（zip，解压即用）。

包内组成：原生桌面窗口启动器 + 裁剪版 CPython 运行时 + 运行依赖（取自 backend/.venv，
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
DOCS = REPO / "docs"
# 随包分发的用户文档（文件名含中文，保持原名便于直接打开）
BUNDLE_DOCS = (
    "ComfyBridge-运维-使用说明手册.html",
    "ComfyBridge-运维-Modal密钥获取指南.html",
)

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
# 这些是运行时依赖，即使名字接近开发工具也不能排除。
SITE_RUNTIME_ALLOW = ("google", "protobuf", "grpc", "grpclib")


def is_dev_package_name(name: str) -> bool:
    """只排除开发工具目录及其 dist-info，避免 startswith 误伤 google 等运行时包。"""
    lower = name.lower()
    if any(lower == keep or lower.startswith(f"{keep}-") for keep in SITE_RUNTIME_ALLOW):
        return False
    return name == "__pycache__" or any(
        lower == prefix or lower.startswith(f"{prefix}-")
        for prefix in SITE_DEV_PREFIXES
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


def assemble_runtime(base: Path, runtime: Path) -> None:
    runtime.mkdir(parents=True)
    if sys.platform == "win32":
        for path in base.iterdir():
            if path.name == "python.exe" or path.name == "LICENSE.txt" or path.suffix.lower() == ".dll":
                # vcruntime DLL 带有系统/AV 文件锁，逐字节复制到新文件可避开 copy2 的元数据写入失败。
                shutil.copyfile(path, runtime / path.name)
        shutil.copytree(base / "DLLs", runtime / "DLLs")
        shutil.copytree(base / "Lib", runtime / "Lib", ignore=lib_ignore(base / "Lib"))
        return
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, runtime / dll.name)
    shutil.copy2(base / "python.exe", runtime / "python.exe")
    lic = base / "LICENSE.txt"
    if lic.is_file():
        shutil.copy2(lic, runtime / "LICENSE.txt")
    shutil.copytree(base / "DLLs", runtime / "DLLs")
    shutil.copytree(base / "Lib", runtime / "Lib", ignore=lib_ignore(base / "Lib"))


def site_ignore(_directory: str, names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name.endswith(".pyc") or is_dev_package_name(name)
    ]


def usage_text(version: str) -> str:
    return f"""ComfyBridge 桌面版 {version}（解压即用，不需要安装）

1. 解压到任意目录（建议路径不含空格与中文）。
2. 双击 comfy-bridge.exe 打开 ComfyBridge 桌面应用；关闭窗口即停止内置服务。
3. 默认仅监听 127.0.0.1:18189；被占用时桌面模式自动尝试 18190-18193，显式 --port 不回退。
4. 端口冲突排查：Get-NetTCPConnection -LocalPort 18189；可用 --port 指定其他端口。
5. 自检：comfy-bridge.exe --doctor    版本：comfy-bridge.exe --version
6. 脚本或排障：comfy-bridge.exe --console [CLI 参数…]

说明：
- 桌面窗口使用系统 WebView2；Windows 10/11 通常已自带，缺失时按系统提示安装。
- 本整合包不含 ComfyUI 本体；出图数据面仍由你已有的本地 ComfyUI 或 Modal 云端提供。
- runtime/ 是内置 Python 运行环境，请勿单独改动；彻底卸载只需删除解压目录。
- ComfyUI 路径、Modal 凭证等配置存放在用户目录 ~/.comfybridge，与整合包目录无关。
- docs/ 内为使用说明手册与 Modal 密钥获取指南，可直接用浏览器打开。
"""


def bundle_version() -> str:
    """整合包版本号：git describe（如 v0.1.0-beta.4），写包根 version.txt 供更新检查。"""
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=REPO, capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() or sys.exit("git describe 返回空版本号")
    except subprocess.CalledProcessError:
        sys.exit("当前 HEAD 未打 tag（先 git tag 再打包，version.txt 供启动时更新检查使用）")


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


def copy_docs(stage: Path) -> None:
    """把用户文档放进整合包；缺失即失败，避免发布缺文档的包。"""
    target = stage / "docs"
    target.mkdir(parents=True, exist_ok=True)
    missing = [name for name in BUNDLE_DOCS if not (DOCS / name).is_file()]
    if missing:
        sys.exit(f"缺少待打包文档: {missing}（位于 {DOCS}）")
    for name in BUNDLE_DOCS:
        shutil.copy2(DOCS / name, target / name)


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

    # staging 放仓库 tmp 下：避开系统临时目录的权限重定向，同时仍被 .gitignore 排除。
    tmp_root = REPO / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="cb-bundle-", dir=tmp_root))
    bundle = workdir
    stage = bundle / f"ComfyBridge-{version}"
    runtime = stage / "runtime"

    try:
        print(f"[1/7] 组装 Python 运行时: {base} -> {runtime}", flush=True)
        assemble_runtime(base, runtime)

        print("[2/7] 复制运行依赖", flush=True)
        if not VENV_SITE.is_dir():
            sys.exit(f"未找到 {VENV_SITE}；先执行 uv sync 初始化开发环境")
        site_dst = runtime / "Lib" / "site-packages"
        shutil.copytree(VENV_SITE, site_dst, ignore=site_ignore)

        print("[3/7] 解包应用 wheel", flush=True)
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(runtime / "Lib" / "site-packages")

        print("[4/7] 构建 Rust 桌面启动器", flush=True)
        cargo = shutil.which("cargo")
        if not cargo:
            sys.exit("未找到 cargo（需要 Rust 工具链）")
        run([cargo, "build", "--release"], cwd=REPO / "packager")
        exe_name = "comfy-bridge.exe" if sys.platform == "win32" else "comfy-bridge"
        built = REPO / "packager" / "target" / "release" / exe_name
        if not built.is_file():
            sys.exit(f"cargo 产物缺失: {built}")
        shutil.copy2(built, stage / exe_name)

        print("[5/7] 写入版本与用户文档", flush=True)
        (stage / "version.txt").write_text(bundle_version(), encoding="ascii")

        (stage / "使用说明.txt").write_text(usage_text(version), encoding="utf-8-sig")

        copy_docs(stage)

        print("[6/7] 包内运行时自检", flush=True)
        python = runtime / "python.exe" if sys.platform == "win32" else runtime / "bin" / "python3"
        smoke_check(python)

        print("[7/7] 生成 zip", flush=True)
        out = BACKEND / "dist" / f"ComfyBridge-{version}-win64.zip"
        make_zip(bundle, stage, out)
        print(f"OK {out.name}  ({out.stat().st_size / 1024 / 1024:.1f} MiB)  app={wheel.name}  python={base.name}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()

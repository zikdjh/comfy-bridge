"""节点清单与同步 API。

- GET  /nodes/local             本地清单（运行中 /object_info + custom_nodes/ 目录扫描）
- GET  /nodes/cloud             云端清单（Modal 数据卷 custom_nodes/ 免容器列举，modal volume ls）
- GET  /nodes/diff?deep=        差异矩阵：包级并集对比；deep=1 时同时拉云端 /object_info
                                （容器未运行会触发冷启动计费，故仅由前端按钮显式触发）
- POST /nodes/sync              安装本地节点包/单文件 → 云端数据卷（整目录 volume put）
- POST /nodes/cloud/remove      从云端数据卷移除节点（volume rm）

同步语义：节点文件落卷后立即可见于云端清单；Python 依赖由 runner 在 comfyui 容器
冷启动时按 requirements.txt 自动安装（install_custom_node_deps），因此同步后需等下次
冷启动（或手动「停止容器 + 预热」重启）才真正生效。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...core import comfy_client
from ...core.config import custom_nodes_dir, get_settings, modal_configured
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError
from ...runner import client as runner_client

router = APIRouter()

_DISABLED_SUFFIX = ".disabled"          # ComfyUI-Manager 停用约定：目录名追加 .disabled
_SKIP_NAMES = {"__pycache__", "_incoming"}  # 卷上与同步都不应出现的杂项目录


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
                    "enabled": not entry.name.endswith(_DISABLED_SUFFIX),
                }
            )
        elif entry.suffix == ".py":
            single_files.append(entry.name)
    return {"path": str(root), "packages": packages, "single_files": single_files}


async def _local_class_types() -> list[str] | None:
    """本地全量 class_type；本地服务未启动返回 None。"""
    try:
        info = await comfy_client.fetch_object_info()
        return sorted(info.keys())
    except Exception:  # noqa: BLE001 —— 服务未启动是正常状态
        return None


@router.get("/nodes/local")
async def nodes_local() -> dict:
    settings = get_settings().local
    online = False
    version = None
    class_types: list[str] = []
    error = None
    try:
        info = await comfy_client.fetch_object_info()
        class_types = sorted(info.keys())
        online = True
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or type(exc).__name__

    try:
        stats = await comfy_client.fetch_system_stats()
        version = stats.get("system", {}).get("comfyui_version")
        online = True
    except Exception:  # noqa: BLE001
        pass

    return {
        "online": online,
        "version": version,
        "error": error,
        "comfy_url": settings.comfy_url,
        "total_class_types": len(class_types),
        "class_types": class_types,
        "class_types_sample": class_types[:40],
        "custom_nodes": await asyncio.to_thread(scan_custom_nodes),
    }


def _safe_name(name: str) -> bool:
    return bool(name) and not name.startswith(".") and "/" not in name and "\\" not in name and ".." not in name


async def _cloud_listing() -> dict:
    """数据卷 custom_nodes/ 的一层清单；目录不存在（新卷/容器未启动过）视为空。"""
    view = {
        "ok": False,
        "error": None,
        "volume": volume_cli.volume_name(),
        "app_state": None,
        "packages": [],       # [{name, size}]
        "single_files": [],   # [{name, size}]
    }
    if not modal_configured():
        view["error"] = "未配置 Modal 凭证（设置 → Modal）"
        return view
    view["app_state"] = await runner_client.ensure_state()
    try:
        entries = await volume_cli.list_dir("custom_nodes")
    except ModalCliError as exc:
        if "No such file or directory" in str(exc):
            view["ok"] = True  # 卷上还没有 custom_nodes/ 目录 = 云端尚未安装任何节点
            return view
        view["error"] = str(exc)
        return view
    for e in entries:
        if e["name"] in _SKIP_NAMES or e["name"].startswith("."):
            continue
        if e["type"] in ("dir", "directory"):
            view["packages"].append({"name": e["name"], "size": e["size"]})
        elif e["name"].endswith(".py"):
            view["single_files"].append({"name": e["name"], "size": e["size"]})
    view["ok"] = True
    return view


@router.get("/nodes/cloud")
async def nodes_cloud() -> dict:
    return await _cloud_listing()


@router.get("/nodes/diff")
async def nodes_diff(deep: bool = Query(False, description="同时拉取云端 class_type（可能触发容器冷启动）")) -> dict:
    scan = await asyncio.to_thread(scan_custom_nodes)
    local: dict[str, dict] = {}
    for p in scan["packages"]:
        local[p["name"]] = {**p, "kind": "pkg", "single": False}
    for f in scan["single_files"]:
        local[f] = {
            "name": f, "repo": None, "commit": None,
            "has_requirements": False, "enabled": True, "kind": "single", "single": True,
        }

    cloud = await _cloud_listing()
    cloud_pkgs = {p["name"] for p in cloud["packages"]}
    cloud_files = {p["name"] for p in cloud["single_files"]}

    rows: list[dict] = []
    for name in sorted(set(local) | cloud_pkgs | cloud_files):
        lp = local.get(name)
        rows.append({
            "name": name,
            "kind": lp["kind"] if lp else ("single" if name in cloud_files else "pkg"),
            "repo": lp["repo"] if lp else None,
            "commit": lp["commit"] if lp else None,
            "has_requirements": bool(lp and lp["has_requirements"]),
            "enabled": bool(lp and lp["enabled"]),
            "local": lp is not None,
            "cloud": name in cloud_pkgs or name in cloud_files,
            "cloud_size": next(
                (p["size"] for p in [*cloud["packages"], *cloud["single_files"]] if p["name"] == name),
                None,
            ),
        })

    only_local = sum(1 for r in rows if r["local"] and not r["cloud"])
    only_cloud = sum(1 for r in rows if r["cloud"] and not r["local"])

    cloud_class_types: list[str] | None = None
    deep_error: str | None = None
    if deep and cloud["ok"]:
        try:
            info = await runner_client.fetch_object_info()
            cloud_class_types = sorted(info.keys())
        except Exception as exc:  # noqa: BLE001 —— 冷启动超时/容器不可达
            deep_error = str(exc) or type(exc).__name__

    return {
        "ok": cloud["ok"],
        "cloud_error": cloud["error"],
        "app_state": cloud["app_state"],
        "volume": cloud["volume"],
        "rows": rows,
        "counts": {
            "synced": len(rows) - only_local - only_cloud,
            "only_local": only_local,
            "only_cloud": only_cloud,
        },
        "class_types": {
            "local": await _local_class_types(),
            "cloud": cloud_class_types,
            "error": deep_error,
        },
    }


class NamesBody(BaseModel):
    names: list[str]


def _stage_dir(src: Path) -> Path:
    """复制到临时目录再上传：排除 __pycache__/.git 等（volume put 不支持排除规则）。"""
    tmp_root = Path(tempfile.mkdtemp(prefix="cb-sync-"))
    staged = tmp_root / src.name
    shutil.copytree(
        src, staged,
        ignore=shutil.ignore_patterns("__pycache__", ".git", ".gitignore", "*.pyc", ".mypy_cache"),
    )
    return staged


async def _volume_put(local_path: Path, remote: str) -> None:
    await volume_cli.run("volume", "put", volume_cli.volume_name(), str(local_path), remote, "-f")


@router.post("/nodes/sync")
async def nodes_sync(body: NamesBody) -> dict:
    root = custom_nodes_dir()
    results: list[dict] = []
    for name in body.names:
        if not _safe_name(name) or name.endswith(_DISABLED_SUFFIX):
            results.append({"name": name, "ok": False, "error": "非法或已停用的节点名"})
            continue
        src = root / name
        if not src.exists():
            results.append({"name": name, "ok": False, "error": "本地不存在"})
            continue
        try:
            remote = f"custom_nodes/{name}"
            if src.is_dir():
                staged = _stage_dir(src)
                try:
                    await _volume_put(staged, remote)
                finally:
                    shutil.rmtree(staged.parent, ignore_errors=True)
            else:
                await _volume_put(src, remote)
            results.append({"name": name, "ok": True, "remote": remote})
        except ModalCliError as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
    return {
        "ok": bool(results) and all(r["ok"] for r in results),
        "results": results,
        "note": "节点文件已写入云端数据卷；Python 依赖会在云端容器下次冷启动时按 requirements.txt 自动安装（可立即「停止容器 + 预热」使其生效）。注意：卷清单有 1~2 分钟最终一致性延迟，差异矩阵可能稍后才显示「已同步」。",
    }


@router.post("/nodes/cloud/remove")
async def nodes_cloud_remove(body: NamesBody) -> dict:
    results: list[dict] = []
    for name in body.names:
        if not _safe_name(name):
            results.append({"name": name, "ok": False, "error": "非法节点名"})
            continue
        remote = f"custom_nodes/{name}"
        try:
            try:
                await volume_cli.run("volume", "rm", volume_cli.volume_name(), remote)
            except ModalCliError:
                # 目录必须 -r；文件不带 -r 即可。重试覆盖两种情况。
                await volume_cli.run("volume", "rm", volume_cli.volume_name(), remote, "-r")
            results.append({"name": name, "ok": True})
        except ModalCliError as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
    return {"ok": bool(results) and all(r["ok"] for r in results), "results": results}

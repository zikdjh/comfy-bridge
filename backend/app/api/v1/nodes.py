"""节点清单与同步 API。

- GET  /nodes/local             本地清单（运行中 /object_info + custom_nodes/ 目录扫描）
- GET  /nodes/cloud             云端清单（Modal 数据卷 custom_nodes/ 免容器列举，modal volume ls）
- GET  /nodes/diff?deep=        差异矩阵：包级并集对比；deep=1 时同时拉云端 /object_info
                                （容器未运行会触发冷启动计费，故仅由前端按钮显式触发）
- POST /nodes/sync              安装本地节点包/单文件 → 云端数据卷（整目录 volume put）
- POST /nodes/cloud/remove      从云端数据卷移除节点（volume rm）
- GET  /nodes/version           本地 ComfyUI 版本 vs 云端镜像 pin 对照 + 部署任务状态
- POST /nodes/version/align     版本对齐（本地为权威）：写 pin 锁文件 → modal deploy 重建镜像
- POST /nodes/image/rebuild     镜像固化（M2-2）：生成 nodes.lock.yaml + install_nodes.sh
                                （启用节点依赖预装进镜像）→ modal deploy，失败自动回滚
                                （部署任务体与互斥见 nodes_mgr.deploy，两者共用单飞通道）

同步语义：节点文件落卷后立即可见于云端清单；Python 依赖由 runner 在 comfyui 容器
冷启动时按 requirements.txt 自动安装（install_custom_node_deps），因此同步后需等下次
冷启动（或手动「停止容器 + 预热」重启）才真正生效。ComfyUI 本体版本不走数据卷，
只能经镜像重建对齐（version/align）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core import comfy_client
from ...core.config import custom_nodes_dir, get_settings, modal_configured
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError
from ...nodes_mgr import deploy, image_rebuild, version_align
from ...nodes_mgr.scan import DISABLED_SUFFIX, scan_custom_nodes
from ...runner import client as runner_client

router = APIRouter()

_SKIP_NAMES = {"__pycache__", "_incoming"}  # 卷上与同步都不应出现的杂项目录
_MANIFEST_REMOTE = "custom_nodes/.comfybridge-manifest.json"


def _manifest(scan: dict | None = None) -> dict:
    """Build a portable inventory manifest with package source and enabled state."""
    scan = scan or scan_custom_nodes()
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "packages": sorted(
            [
                {
                    "name": p["name"],
                    "kind": "pkg",
                    "repo": p["repo"],
                    "commit": p["commit"],
                    "has_requirements": p["has_requirements"],
                    "enabled": p["enabled"],
                }
                for p in scan["packages"]
            ],
            key=lambda p: p["name"],
        ),
        "single_files": sorted(
            [
                {
                    "name": name,
                    "kind": "single",
                    "repo": None,
                    "commit": None,
                    "has_requirements": False,
                    "enabled": True,
                }
                for name in scan["single_files"]
            ],
            key=lambda p: p["name"],
        ),
    }


def _manifest_entries(manifest: dict | None) -> dict[str, dict]:
    if not isinstance(manifest, dict):
        return {}
    return {
        item["name"]: item
        for item in [*manifest.get("packages", []), *manifest.get("single_files", [])]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


async def _read_cloud_manifest() -> dict | None:
    """Read the inventory manifest without starting a Modal container."""
    tmp_root = Path(tempfile.mkdtemp(prefix="cb-node-manifest-"))
    dest = tmp_root / "manifest.json"
    try:
        await volume_cli.run(
            "volume", "get", volume_cli.volume_name(), _MANIFEST_REMOTE, str(dest)
        )
        return json.loads(dest.read_text(encoding="utf-8"))
    except (ModalCliError, OSError, json.JSONDecodeError):
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


async def _write_cloud_manifest(manifest: dict) -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="cb-node-manifest-write-"))
    manifest_path = tmp_root / "manifest.json"
    try:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await _volume_put(manifest_path, _MANIFEST_REMOTE)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


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
        "manifest": None,
    }
    if not modal_configured():
        view["error"] = "未配置 Modal 凭证（设置 → Modal）"
        return view
    view["manifest"] = await _read_cloud_manifest()
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
    local_manifest = _manifest(scan)
    local: dict[str, dict] = {}
    for p in scan["packages"]:
        local[p["name"]] = {**p, "kind": "pkg", "single": False}
    for f in scan["single_files"]:
        local[f] = {
            "name": f, "repo": None, "commit": None,
            "has_requirements": False, "enabled": True, "kind": "single", "single": True,
        }

    cloud = await _cloud_listing()
    cloud_manifest = cloud.get("manifest")
    if cloud_manifest is None and cloud["ok"]:
        cloud_manifest = {
            "schema": 0,
            "packages": [
                {"name": p["name"], "kind": "pkg", "repo": None, "commit": None,
                 "has_requirements": False, "enabled": True}
                for p in cloud["packages"]
            ],
            "single_files": [
                {"name": p["name"], "kind": "single", "repo": None, "commit": None,
                 "has_requirements": False, "enabled": True}
                for p in cloud["single_files"]
            ],
        }
    cloud_entries = _manifest_entries(cloud_manifest)
    cloud_pkgs = {p["name"] for p in cloud["packages"]}
    cloud_files = {p["name"] for p in cloud["single_files"]}

    rows: list[dict] = []
    for name in sorted(set(local) | cloud_pkgs | cloud_files):
        lp = local.get(name)
        cp = cloud_entries.get(name)
        cloud_has = name in cloud_pkgs or name in cloud_files
        if lp and not cloud_has:
            state = "missing_cloud"
        elif cloud_has and not lp:
            state = "missing_local"
        elif lp and cp and (
            lp.get("commit") != cp.get("commit")
            or lp.get("enabled") != cp.get("enabled")
            or lp.get("kind") != cp.get("kind")
        ):
            state = "changed"
        else:
            state = "same" if lp and cloud_has else "unknown"
        rows.append({
            "name": name,
            "kind": lp["kind"] if lp else ("single" if name in cloud_files else "pkg"),
            "repo": lp["repo"] if lp else None,
            "commit": lp["commit"] if lp else None,
            "has_requirements": bool(lp and lp["has_requirements"]),
            "enabled": bool(lp and lp["enabled"]),
            "local": lp is not None,
            "cloud": cloud_has,
            "cloud_size": next(
                (p["size"] for p in [*cloud["packages"], *cloud["single_files"]] if p["name"] == name),
                None,
            ),
            "cloud_commit": cp.get("commit") if cp else None,
            "cloud_enabled": cp.get("enabled") if cp else None,
            "state": state,
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
        "manifest": {"local": local_manifest, "cloud": cloud_manifest},
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
        if not _safe_name(name):
            results.append({"name": name, "ok": False, "error": "非法节点名"})
            continue
        src = root / name
        if not src.exists():
            results.append({"name": name, "ok": False, "error": "本地不存在"})
            continue
        try:
            # 停用节点（ComfyUI-Manager 约定：目录名追加 .disabled）按实际目录名整包落卷，
            # ComfyUI 核心加载 custom_nodes 时跳过 .disabled 目录，冷启动装依赖时同样跳过
            remote = f"custom_nodes/{name}"
            if src.is_dir():
                staged = _stage_dir(src)
                try:
                    await _volume_put(staged, remote)
                finally:
                    shutil.rmtree(staged.parent, ignore_errors=True)
            else:
                await _volume_put(src, remote)
            results.append({
                "name": name, "ok": True, "remote": remote,
                "enabled": not name.endswith(DISABLED_SUFFIX),
            })
        except ModalCliError as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
    if any(r["ok"] for r in results):
        current = _manifest(await asyncio.to_thread(scan_custom_nodes))
        local_entries = _manifest_entries(current)
        existing = await _read_cloud_manifest() or {
            "schema": 0, "packages": [], "single_files": []
        }
        combined = _manifest_entries(existing)
        for result in (r for r in results if r["ok"]):
            if result["name"] in local_entries:
                combined[result["name"]] = {
                    **local_entries[result["name"]],
                    "enabled": result.get("enabled", True),
                }
        await _write_cloud_manifest({
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "packages": sorted(
                (v for v in combined.values() if v["kind"] == "pkg"),
                key=lambda p: p["name"],
            ),
            "single_files": sorted(
                (v for v in combined.values() if v["kind"] == "single"),
                key=lambda p: p["name"],
            ),
        })
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
            await _remove_remote(remote)
            if not name.endswith(DISABLED_SUFFIX):
                await _remove_remote(f"custom_nodes/{name}{DISABLED_SUFFIX}")
            results.append({"name": name, "ok": True})
        except ModalCliError as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
    # manifest 条目以实际名字（含 .disabled 后缀）记录，按名精确清理
    successful = {r["name"] for r in results if r["ok"]}
    if successful:
        manifest = await _read_cloud_manifest()
        if manifest is not None:
            manifest["packages"] = [
                p for p in manifest.get("packages", []) if p["name"] not in successful
            ]
            manifest["single_files"] = [
                p for p in manifest.get("single_files", []) if p["name"] not in successful
            ]
            manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
            await _write_cloud_manifest(manifest)
    return {"ok": bool(results) and all(r["ok"] for r in results), "results": results}


async def _remove_remote(remote: str) -> None:
    try:
        await volume_cli.run("volume", "rm", volume_cli.volume_name(), remote)
    except ModalCliError:
        # Directories require recursive removal; retry covers both files and dirs.
        await volume_cli.run("volume", "rm", volume_cli.volume_name(), remote, "-r")


# ---------------------------------------------------------------- 版本对齐与镜像固化

@router.get("/nodes/version")
async def nodes_version() -> dict:
    """本地 ComfyUI 版本 vs 云端镜像 pin 对照 + 部署任务状态（纯文件读，可轮询）。"""
    return {
        **version_align.status(get_settings().local.comfy_root),
        "app_name": get_settings().modal.app_name,
        "job": deploy.job_state(),
    }


@router.post("/nodes/version/align")
async def nodes_version_align() -> dict:
    """把云端镜像 pin 对齐到本地版本并 modal deploy 重建（后台任务，失败回滚 pin）。"""
    if not modal_configured():
        raise HTTPException(status_code=400, detail="未配置 Modal 凭证（设置 → Modal）")
    comfy_root = get_settings().local.comfy_root
    try:
        job = await version_align.start_align(comfy_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": job["state"] in ("running", "ok"), "job": job}


@router.post("/nodes/image/rebuild")
async def nodes_image_rebuild() -> dict:
    """镜像固化（M2-2）：按本地节点集生成 nodes.lock.yaml + install_nodes.sh 并重建镜像。"""
    if not modal_configured():
        raise HTTPException(status_code=400, detail="未配置 Modal 凭证（设置 → Modal）")
    try:
        job = await image_rebuild.start_rebuild()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": job["state"] in ("running", "ok"), "job": job}

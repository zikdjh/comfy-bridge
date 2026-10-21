"""节点清单与同步 API。

- GET  /nodes/local             本地清单（运行中 /object_info + custom_nodes/ 目录扫描
                                + ComfyUI-Manager API 兜底增强：在线且装了 Manager 时补
                                非 git 包的 repo/版本/展示名，任何失败静默跳过）
- GET  /nodes/cloud             云端清单（Modal 数据卷 custom_nodes/ 免容器列举，SDK list_dir）
- GET  /nodes/diff?deep=&force= 差异矩阵：包级并集对比；deep=1 时同时拉云端 /object_info
                                （容器未运行会触发冷启动计费，故仅由前端按钮显式触发）；
                                矩阵短 TTL 缓存、deep class_type 块持久化缓存（~/.comfybridge/
                                nodes_diff_cache.json，重启不丢），force=1 绕过缓存现算
- POST /nodes/sync              安装本地节点包/单文件 → 云端数据卷（整目录上传）
- POST /nodes/cloud/remove      从云端数据卷移除节点
- GET  /nodes/version           本地 ComfyUI 版本 vs 云端镜像 pin 对照 + 部署任务状态
- POST /nodes/version/align     版本对齐（本地为权威）：写 pin 锁文件 → modal deploy 重建镜像
- GET  /nodes/dependency-precheck
                               重建前依赖预检：静态约束 + pip dry-run resolver，返回
                               ok / blocked / unavailable 及涉及的节点与约束
- POST /nodes/image/rebuild     镜像重建：固化（M2-2，nodes.lock.yaml + install_nodes.sh 依赖预装）
                                + 可选版本对齐（align_version）合并为一次 modal deploy，失败自动回滚
                                （部署任务体与互斥见 nodes_mgr.deploy，与 version/align 共用单飞通道）

同步语义：节点文件落卷后立即可见于云端清单；Python 依赖由 runner 在 comfyui 容器
冷启动时按 requirements.txt 自动安装（install_custom_node_deps），因此同步后需等下次
冷启动（或手动「停止容器 + 预热」重启）才真正生效。ComfyUI 本体版本不走数据卷，
只能经镜像重建对齐（version/align）。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core import comfy_client
from ...core.config import config_dir, custom_nodes_dir, get_settings, modal_configured
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError
from ...models_mgr import node_presence
from ...nodes_mgr import (
    deploy,
    diff_cache,
    image_rebuild,
    lockfile,
    manager_api,
    resolve,
    version_align,
)
from ...nodes_mgr.scan import DISABLED_SUFFIX, scan_custom_nodes
from ...proxy import object_info as object_info_merge
from ...runner import client as runner_client
from ...sync import jobs

router = APIRouter()

_SKIP_NAMES = {"__pycache__", "_incoming"}  # 卷上与同步都不应出现的杂项目录
_MANIFEST_REMOTE = "custom_nodes/.comfybridge-manifest.json"


def _manifest(scan: dict | None = None) -> dict:
    """Build a portable inventory manifest with package source and enabled state."""
    scan = scan or scan_custom_nodes()
    return {
        "schema": 1,
        "generated_at": datetime.now(UTC).isoformat(),
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
        await volume_cli.get_file(_MANIFEST_REMOTE, dest, force=True)
        return json.loads(dest.read_text(encoding="utf-8"))
    except (ModalCliError, OSError, json.JSONDecodeError):
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


async def _write_cloud_manifest(manifest: dict) -> None:
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="cb-node-manifest-write-"))
    except OSError:
        tmp_root = config_dir() / "tmp" / f"cb-node-manifest-write-{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=True)
    manifest_path = tmp_root / "manifest.json"
    try:
        try:
            manifest_path.write_text(payload, encoding="utf-8")
        except OSError:
            fallback_root = config_dir() / "tmp" / f"cb-node-manifest-write-{uuid.uuid4().hex}"
            fallback_root.mkdir(parents=True, exist_ok=True)
            manifest_path = fallback_root / "manifest.json"
            manifest_path.write_text(payload, encoding="utf-8")
        await _volume_put(manifest_path, _MANIFEST_REMOTE)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        if manifest_path.parent != tmp_root:
            shutil.rmtree(manifest_path.parent, ignore_errors=True)


async def _local_class_types() -> list[str] | None:
    """本地全量 class_type；本地服务未启动返回 None。"""
    try:
        info = await comfy_client.fetch_object_info()
        return sorted(info.keys())
    except Exception:  # noqa: BLE001 —— 服务未启动是正常状态
        return None


async def _local_object_info() -> dict | None:
    """本地全量 object_info schema；本地服务未启动返回 None。"""
    try:
        info = await comfy_client.fetch_object_info()
        return info if isinstance(info, dict) else None
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

    with contextlib.suppress(Exception):  # 取版本失败不影响在线判定
        stats = await comfy_client.fetch_system_stats()
        version = stats.get("system", {}).get("comfyui_version")
        online = True

    # Manager API 兜底（M2-1 余项）：在线且装有 ComfyUI-Manager 时增强目录扫描，
    # 补非 git 包的 repo/版本/展示名；离线或失败静默跳过，不影响主清单
    custom_nodes = await asyncio.to_thread(scan_custom_nodes)
    mgr = await manager_api.fetch_index()
    mgr_view = {"online": mgr["online"], "entries": mgr["entries"], "error": mgr["error"]}
    if mgr["online"]:
        mgr_view["matched"] = manager_api.apply_enrichment(custom_nodes, mgr["index"])

    return {
        "online": online,
        "version": version,
        "error": error,
        "comfy_url": settings.comfy_url,
        "total_class_types": len(class_types),
        "class_types": class_types,
        "class_types_sample": class_types[:40],
        "manager": mgr_view,
        "custom_nodes": custom_nodes,
    }


def _safe_name(name: str) -> bool:
    return bool(name) and not name.startswith(".") and "/" not in name and "\\" not in name and ".." not in name


async def _cloud_listing() -> dict:
    """数据卷 custom_nodes/ 的一层清单；目录不存在（新卷/容器未启动过）视为空。"""
    view: dict[str, Any] = {
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


def _local_file_hash(name: str) -> str | None:
    """本地单文件节点内容 sha256（文件缺失/不可读返回 None）。"""
    path = custom_nodes_dir() / name
    if not path.is_file():
        return None
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


async def _cloud_file_hash(name: str) -> str | None:
    """云端单文件内容 sha256（SDK 网关现读；失败返回 None，diff 退回元数据对比）。"""
    tmp_root = Path(tempfile.mkdtemp(prefix="cb-node-hash-"))
    try:
        dest = tmp_root / name
        await volume_cli.get_file(f"custom_nodes/{name}", dest, force=True)
        h = hashlib.sha256()
        with dest.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (ModalCliError, OSError):
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _diff_state(
    lp: dict | None, cp: dict | None, cloud_has: bool,
    local_hash: str | None, cloud_hash: str | None,
) -> str:
    """一致性判定（纯函数）：包级比 commit/启用/形态；单文件无 commit 可比时以内容哈希为准。"""
    if lp and not cloud_has:
        return "missing_cloud"
    if cloud_has and not lp:
        return "missing_local"
    if lp and cp and (
        lp.get("commit") != cp.get("commit")
        or lp.get("enabled") != cp.get("enabled")
        or lp.get("kind") != cp.get("kind")
    ):
        return "changed"
    if (
        lp and cloud_has and lp.get("kind") == "single"
        and local_hash and cloud_hash and local_hash != cloud_hash
    ):
        return "changed"  # 元数据一致但内容不同（单文件无 commit，哈希为准）
    return "same" if lp and cloud_has else "unknown"


async def _compute_matrix() -> dict:
    """差异矩阵现算（本地目录扫描 + 云端卷清单，含单文件内容哈希对比）。"""
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
        # 单文件节点没有 commit 可比：两端现算内容 sha256 判漂移（M2 内容哈希检测收尾）
        local_hash = cloud_hash = None
        if lp and cloud_has and lp["kind"] == "single":
            local_hash = _local_file_hash(name)
            cloud_hash = await _cloud_file_hash(name)
        state = _diff_state(lp, cp, cloud_has, local_hash, cloud_hash)
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
            "cloud_repo": cp.get("repo") if cp else None,
            "cloud_enabled": cp.get("enabled") if cp else None,
            "local_hash": local_hash[:12] if local_hash else None,
            "cloud_hash": cloud_hash[:12] if cloud_hash else None,
            "state": state,
        })

    only_local = sum(1 for r in rows if r["local"] and not r["cloud"])
    only_cloud = sum(1 for r in rows if r["cloud"] and not r["local"])

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
    }


@router.get("/nodes/diff")
async def nodes_diff(
    deep: bool = Query(False, description="同时取两端 class_type 对比（无缓存时可能触发容器冷启动）"),
    force: bool = Query(False, description="绕过缓存现算"),
) -> dict:
    matrix = None if force else diff_cache.load_matrix()
    matrix_hit = matrix is not None
    if matrix is None:
        matrix = await _compute_matrix()
        diff_cache.save_matrix(matrix)  # 失败结果（ok=False）不会被缓存
    if matrix_hit:
        # 缓存命中时 app_state 属运行时状态，每次现取（runner_client 自带 60s TTL）
        try:
            matrix["app_state"] = await runner_client.ensure_state()
        except Exception as exc:  # noqa: BLE001
            matrix["app_state"] = f"状态获取失败: {exc}"
    matrix["cached"] = matrix_hit
    matrix["cached_at"] = matrix.get("cached_at") if matrix_hit else None

    if deep:
        # deep class_type 块持久缓存（无 TTL）：避免每次进页面都重新触发云端冷启动计费；
        # 只有 force=1 或块缺失才重拉
        class_types = None if force else diff_cache.load_class_types()
        ct_hit = class_types is not None
        cloud_schemas: dict | None = None
        if class_types is None:
            local_doc = await _local_object_info()
            class_types = {
                "local": sorted(local_doc.keys()) if local_doc is not None else None,
                "cloud": None,
                "error": None,
                "conflicts": {},
                "merge_strategy": object_info_merge.effective_strategy(),
            }
            if matrix.get("ok"):
                try:
                    info = await runner_client.fetch_object_info()
                    class_types["cloud"] = sorted(info.keys())
                    diagnostics = object_info_merge.set_cloud_schemas(info, local_doc or {})
                    class_types["conflicts"] = diagnostics["conflicts"]
                    cloud_schemas = object_info_merge.cloud_schemas_snapshot()
                except Exception as exc:  # noqa: BLE001 —— 冷启动超时/容器不可达
                    class_types["error"] = str(exc) or type(exc).__name__
            diff_cache.save_class_types(class_types)
            if cloud_schemas is not None:
                diff_cache.save_cloud_schemas(cloud_schemas)
        else:
            object_info_merge.restore_cloud_schemas(
                diff_cache.load_cloud_schemas(),
                class_types.get("conflicts") or {},
            )
            class_types["merge_strategy"] = object_info_merge.effective_strategy()
        object_info_merge.invalidate_merge_cache()
        class_types["cached"] = ct_hit
        class_types["cached_at"] = class_types.get("cached_at") if ct_hit else None
    else:
        class_types = {
            "local": await _local_class_types(), "cloud": None, "error": None,
            "conflicts": {}, "merge_strategy": object_info_merge.effective_strategy(),
            "cached": False, "cached_at": None,
        }

    return {**matrix, "class_types": class_types}


class NamesBody(BaseModel):
    names: list[str]


class RebuildBody(BaseModel):
    # align_version=True：把「版本 pin 对齐」并入同一次镜像重建（一次 deploy，M2 入口合并）
    align_version: bool = False


def _stage_dir(src: Path) -> Path:
    """复制到临时目录再上传：排除 __pycache__/.git 等（SDK 上传不做排除规则）。"""
    tmp_root = Path(tempfile.mkdtemp(prefix="cb-sync-"))
    staged = tmp_root / src.name
    shutil.copytree(
        src, staged,
        ignore=shutil.ignore_patterns("__pycache__", ".git", ".gitignore", "*.pyc", ".mypy_cache"),
    )
    return staged


async def _volume_put(local_path: Path, remote: str) -> None:
    await volume_cli.put_file(local_path, remote, force=True)


_SYNC_LOCK = asyncio.Lock()  # 串行化同步作业：manifest 合并与卷写入不并发交错


async def _sync_body(handle, names: list[str]) -> None:
    """同步作业体：逐个上传节点并推进作业条目，成功后合并 manifest + 失效矩阵缓存。"""
    root = custom_nodes_dir()
    results: list[dict] = []
    async with _SYNC_LOCK:
        for name in names:
            handle.check_cancel()
            handle.phase(f"准备 {name}")
            if not _safe_name(name):
                result = {"name": name, "ok": False, "error": "非法节点名"}
            else:
                src = root / name
                if not src.exists():
                    result = {"name": name, "ok": False, "error": "本地不存在"}
                else:
                    try:
                        # 停用节点（ComfyUI-Manager 约定：目录名追加 .disabled）按实际目录名整包落卷，
                        # ComfyUI 核心加载 custom_nodes 时跳过 .disabled 目录，冷启动装依赖时同样跳过
                        remote = f"custom_nodes/{name}"
                        if src.is_dir():
                            handle.log(f"打包上传 {name} …")
                            handle.phase(f"上传 {name}")
                            staged = _stage_dir(src)
                            try:
                                await volume_cli.put_directory(staged, remote, force=True)
                            finally:
                                shutil.rmtree(staged.parent, ignore_errors=True)
                        else:
                            await _volume_put(src, remote)
                        result = {
                            "name": name, "ok": True, "remote": remote,
                            "enabled": not name.endswith(DISABLED_SUFFIX),
                        }
                    except ModalCliError as exc:
                        result = {"name": name, "ok": False, "error": str(exc)}
            results.append(result)
            handle.advance(result)
            handle.phase(f"完成 {name}")
            handle.log(f"{name} → {'已写入数据卷' if result['ok'] else result.get('error', '失败')}")
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
                "generated_at": datetime.now(UTC).isoformat(),
                "packages": sorted(
                    (v for v in combined.values() if v["kind"] == "pkg"),
                    key=lambda p: p["name"],
                ),
                "single_files": sorted(
                    (v for v in combined.values() if v["kind"] == "single"),
                    key=lambda p: p["name"],
                ),
            })
            diff_cache.invalidate_matrix()  # 卷上节点已变，矩阵缓存失效，下次请求现算


@router.post("/nodes/sync")
async def nodes_sync(body: NamesBody) -> dict:
    """同步节点到云端数据卷（M2-6 任务化）：立即返回 job，进度经 GET /jobs 轮询。"""
    if not body.names:
        return {"ok": False, "error": "没有要同步的节点", "job": None}
    job = jobs.start_job(
        "node_sync", f"节点同步（{len(body.names)} 项）", len(body.names),
        lambda handle: _sync_body(handle, body.names), cancellable=True,
    )
    return {
        "ok": True,
        "job": job,
        "note": "同步任务已提交，进度见顶部「任务」抽屉；Python 依赖会在云端容器下次冷启动时按 requirements.txt 自动安装（可立即「停止容器 + 预热」使其生效）。卷清单有 1~2 分钟最终一致性延迟，差异矩阵可能稍后才显示「已同步」。",
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
                # .disabled 变体多数情况下不存在，缺文件不算失败
                try:
                    await _remove_remote(f"custom_nodes/{name}{DISABLED_SUFFIX}")
                except ModalCliError as exc:
                    if "No such file or directory" not in str(exc):
                        raise
            results.append({"name": name, "ok": True})
        except ModalCliError as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
    # manifest 条目以实际名字（含 .disabled 后缀）记录，按名精确清理
    successful = {r["name"] for r in results if r["ok"]}
    if successful:
        diff_cache.invalidate_matrix()  # 卷上节点已变，矩阵缓存失效
        manifest = await _read_cloud_manifest()
        if manifest is not None:
            manifest["packages"] = [
                p for p in manifest.get("packages", []) if p["name"] not in successful
            ]
            manifest["single_files"] = [
                p for p in manifest.get("single_files", []) if p["name"] not in successful
            ]
            manifest["generated_at"] = datetime.now(UTC).isoformat()
            await _write_cloud_manifest(manifest)
    return {"ok": bool(results) and all(r["ok"] for r in results), "results": results}


async def _remove_remote(remote: str) -> None:
    await volume_cli.remove_file(remote, recursive=True)


# ---------------------------------------------------------------- 版本对齐与镜像固化

@router.get("/nodes/version")
async def nodes_version() -> dict:
    """本地 ComfyUI 版本 vs 云端镜像 pin 对照 + 部署任务状态 + 对齐历史（纯文件读，可轮询）。"""
    return {
        **version_align.status(get_settings().local.comfy_root),
        "app_name": get_settings().modal.app_name,
        "job": deploy.job_state(),
        "history": version_align.read_history(20),
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


@router.get("/nodes/dependency-precheck")
async def nodes_dependency_precheck(force: bool = Query(False)) -> dict:
    """镜像重建前的节点依赖预检（本地计算，不触发 Modal 容器或 deploy）。"""
    scan = await asyncio.to_thread(scan_custom_nodes)
    lock = lockfile.generate(scan)
    return await asyncio.to_thread(resolve.precheck, lock["nodes"], force=force)


@router.post("/nodes/image/rebuild")
async def nodes_image_rebuild(body: RebuildBody | None = None) -> dict:
    """镜像重建：固化本地节点依赖进镜像；align_version=True 时并入库内 ComfyUI 版本对齐（一次 deploy）。"""
    if not modal_configured():
        raise HTTPException(status_code=400, detail="未配置 Modal 凭证（设置 → Modal）")
    try:
        job = await image_rebuild.start_rebuild(align_version=bool(body and body.align_version))
    except resolve.DependencyConflictError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": job["state"] in ("running", "ok"), "job": job}


@router.post("/nodes/presence/refresh")
async def nodes_presence_refresh() -> dict:
    """强制刷新节点 class 两端在场索引（M3-2；装完节点后手动保鲜，node_missing_* 规则数据源）。"""
    snap = await node_presence.refresh(force=True)
    return {
        "ok": True,
        "local": len(snap["local"]),
        "cloud": len(snap["cloud"]),
        "error": node_presence.last_error(),
    }

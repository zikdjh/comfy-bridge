"""数据卷管理 API：管理 token 鉴权下的所有 Modal 数据卷 + URL 直下 + 目录维护。

- GET  /api/v1/volume/meta                 元信息（默认主卷 / 凭证 / SDK 可用性）
- GET  /api/v1/volume/volumes?refresh=     所有卷 + 递归统计的总用量（缓存 60s，
                                           过期后台重算并置 computing=true，前端轮询）
- POST /api/v1/volume/create               新增数据卷
- GET  /api/v1/volume/list?path=&volume=   列一层目录（相对卷根，"" = 根；默认主卷）
- POST /api/v1/volume/delete               删除文件/目录（目录自动递归）
- POST /api/v1/volume/delete               dry_run=true 只做引用检查，不落盘
- GET  /api/v1/volume/trash                回收站条目列表
- POST /api/v1/volume/trash/restore        从回收站恢复
- POST /api/v1/volume/trash/purge          永久清理回收站条目
- POST /api/v1/volume/mkdir                新建文件夹（.keep 标记置入后删除，留下真空目录）
- GET  /api/v1/volume/dirs?volume=         全卷目录清单（移动目标选择用，BFS 展开）
- POST /api/v1/volume/move                 卷内移动（modal run 容器内服务端复制+删源）
- GET  /api/v1/volume/moves                移动作业列表（内存态）
- POST /api/v1/volume/moves/{id}/cancel
- POST /api/v1/volume/download             创建 URL 云端直下作业（任意卷：modal run 临时应用
                                           经 CB_MODAL_VOLUME 挂载所选卷；主卷为默认）
- GET  /api/v1/volume/downloads            下载作业列表
- POST /api/v1/volume/downloads/{id}/cancel
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.config import get_settings, modal_configured
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError
from ...volume_mgr import deletion, downloads, moves, usage

router = APIRouter(prefix="/volume", tags=["volume"])

# Modal 卷命名：小写字母开头的字母/数字/连字符（3~32 位）
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")

# 主卷顶层结构目录（ComfyUI/runner 运行依赖），不允许移动
_RESERVED_TOP = {"models", "input", "output", "custom_nodes", "user", "config", "temp"}

# 全卷目录清单缓存：卷名 → (单调时钟, 响应体)
_DIRS_TTL = 60.0
_DIRS_CACHE: dict[str, tuple[float, dict]] = {}


def _safe_rel(path: str, *, allow_trash: bool = False) -> str:
    rel = (path or "").replace("\\", "/").strip("/")
    first = rel.split("/")[0] if rel else ""
    hidden = first.startswith(".") and not (allow_trash and first == deletion.TRASH_ROOT)
    if rel.startswith("/") or ".." in rel.split("/") or hidden:
        raise HTTPException(status_code=422, detail=f"非法的数据卷相对路径: {path!r}")
    return rel


def _volume_or_default(volume: str | None) -> str:
    name = (volume or "").strip() or get_settings().modal.volume
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=422, detail=f"非法的数据卷名: {name!r}")
    return name


@router.get("/meta")
async def meta() -> dict:
    return {
        "volume": get_settings().modal.volume,
        "credentials_ok": modal_configured(),
        "cli_found": volume_cli.sdk_available(),
    }


@router.get("/volumes")
async def volumes(refresh: bool = Query(False, description="强制重算用量")) -> dict:
    if not modal_configured():
        return {
            "computing": False, "computed_ms": 0, "total_bytes": 0, "file_count": 0,
            "status": "ok", "volumes": [], "error": "未配置 Modal 凭证（设置 → Modal）",
        }
    return usage.get_usage(force=refresh)


class CreateBody(BaseModel):
    name: str


@router.post("/create")
async def create_volume(body: CreateBody) -> dict:
    name = (body.name or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="卷名需 3~32 位小写字母/数字/连字符，且以字母开头",
        )
    try:
        await volume_cli.volume_create(name)
    except ModalCliError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    usage.get_usage(force=True)  # 新卷立即进入统计
    return {"ok": True, "name": name}


@router.post("/volumes/{name}/delete")
async def delete_volume(name: str) -> dict:
    """删除整卷。主数据卷被 runner 挂载不可删；含文件的卷拒绝删除（先清空）。"""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=422, detail=f"非法的数据卷名: {name!r}")
    if name == get_settings().modal.volume:
        raise HTTPException(status_code=400, detail="主数据卷被 runner 挂载且承载 ComfyUI 数据，不允许在此删除")
    try:
        files = await usage.count_files(name, "")
    except ModalCliError:
        files = 0  # 空卷/清单不可读时不拦删除，由 SDK 删除结果兜底
    if files > 0:
        raise HTTPException(
            status_code=400,
            detail=f"数据卷包含 {files} 个文件，不允许删除。请先清空卷内文件",
        )
    try:
        await volume_cli.volume_delete(name)
    except ModalCliError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    usage.get_usage(force=True)
    return {"ok": True, "name": name}


@router.get("/list")
async def list_dir(
    path: str = Query("", description="相对卷根的目录路径，空 = 根"),
    volume: str | None = Query(None, description="数据卷名，空 = 主卷"),
) -> dict:
    rel = _safe_rel(path)
    vol = _volume_or_default(volume)
    view: dict[str, Any] = {
        "ok": False,
        "error": None,
        "path": rel,
        "volume": vol,
        "entries": [],
    }
    if not modal_configured():
        view["error"] = "未配置 Modal 凭证（设置 → Modal）"
        return view
    try:
        entries = await volume_cli.list_dir(rel or "/", volume=vol)
    except ModalCliError as exc:
        if "No such file or directory" in str(exc):
            view["ok"] = True  # 目录/卷尚未有内容，视为空
            return view
        view["error"] = str(exc)
        return view
    view["entries"] = entries
    view["ok"] = True
    return view


class PathsBody(BaseModel):
    paths: list[str]
    volume: str | None = None
    dry_run: bool = False
    allow_referenced: bool = False


@router.post("/delete")
async def delete_paths(body: PathsBody) -> dict:
    vol = _volume_or_default(body.volume)
    if body.dry_run:
        # 删除预览：只做引用扫描；目录内容守卫仍按真实状态检查。
        preview: list[dict] = []
        for raw in body.paths:
            try:
                rel = _safe_rel(raw)
            except HTTPException as exc:
                preview.append({"path": raw, "ok": False, "error": str(exc.detail)})
                continue
            if not rel:
                preview.append({"path": raw, "ok": False, "error": "不能删除卷根目录"})
                continue
            try:
                kind = await _entry_kind(vol, rel)
            except ModalCliError as exc:
                preview.append({"path": rel, "ok": False, "error": str(exc)})
                continue
            reference = await asyncio.to_thread(deletion.inspect_references, rel)
            preview.append({
                "path": rel,
                "ok": True,
                "kind": kind,
                "reference": reference,
            })
        return {
            "ok": all(r["ok"] for r in preview),
            "dry_run": True,
            "results": preview,
        }

    results: list[dict] = []
    for raw in body.paths:
        try:
            rel = _safe_rel(raw)
        except HTTPException as exc:
            results.append({"path": raw, "ok": False, "error": str(exc.detail)})
            continue
        if not rel:
            results.append({"path": raw, "ok": False, "error": "不能删除卷根目录"})
            continue
        try:
            # 删除守卫：目录含任意文件（递归）时拒绝；文件本身可直接删
            kind = await _entry_kind(vol, rel)
            if kind == "dir":
                try:
                    files = await usage.count_files(vol, rel)
                except ModalCliError as exc:
                    results.append({"path": rel, "ok": False, "error": f"无法校验目录内容: {exc}"})
                    continue
                if files > 0:
                    results.append({
                        "path": rel,
                        "ok": False,
                        "error": f"目录包含 {files} 个文件，不允许删除。请先清空目录内的文件",
                    })
                    continue
            if deletion.is_model_path(rel) and not body.allow_referenced:
                reference = await asyncio.to_thread(deletion.inspect_references, rel)
                if reference["referenced"]:
                    results.append({
                        "path": rel,
                        "ok": False,
                        "error": reference["warning"],
                        "reference": reference,
                    })
                    continue
            if deletion.is_model_path(rel):
                dest = await deletion.move_to_trash(vol, rel, kind)
                results.append({"path": rel, "ok": True, "trash_path": dest})
                continue
            await volume_cli.remove_file(rel, volume=vol, recursive=True)
            results.append({"path": rel, "ok": True})
        except ModalCliError as exc:
            results.append({"path": rel, "ok": False, "error": str(exc)})
    usage.get_usage(force=bool(results) and any(r["ok"] for r in results))
    if any(r["ok"] for r in results):
        _DIRS_CACHE.pop(body.volume or get_settings().modal.volume, None)
    return {
        "ok": bool(results) and all(r["ok"] for r in results),
        "results": results,
        "note": "仅允许删除空目录；模型文件会移入回收站。清单刷新可能有约 1 分钟延迟。",
    }


class TrashRestoreBody(BaseModel):
    path: str
    dest: str | None = None
    volume: str | None = None


@router.get("/trash")
async def trash_list(volume: str | None = Query(None, description="数据卷名，空 = 主卷")) -> dict:
    vol = _volume_or_default(volume)
    try:
        entries = await deletion.list_trash(vol)
    except ModalCliError as exc:
        return {"ok": False, "volume": vol, "entries": [], "error": str(exc)[:300]}
    return {"ok": True, "volume": vol, "entries": entries}


@router.post("/trash/restore")
async def trash_restore(body: TrashRestoreBody) -> dict:
    vol = _volume_or_default(body.volume)
    try:
        rel = _safe_rel(body.path, allow_trash=True)
        dest = _safe_rel(body.dest or "") if body.dest else None
        if not deletion.is_trash_path(rel):
            raise HTTPException(status_code=422, detail="只能从 .trash/<时间戳>/... 恢复")
        kind = await _entry_kind(vol, rel)
        target = dest or rel.split("/", 2)[2]
        if not target:
            raise HTTPException(status_code=422, detail="恢复目标不能为空")
        target_parent = target.rpartition("/")[0]
        target_name = target.rsplit("/", 1)[-1]
        try:
            target_entries = await volume_cli.list_dir(target_parent or "/", volume=vol)
        except ModalCliError:
            target_entries = []
        if any(e.get("name") == target_name for e in target_entries):
            raise HTTPException(status_code=409, detail=f"恢复目标已存在，拒绝覆盖: {target}")
        target = await deletion.restore_from_trash(vol, rel, target, kind)
    except HTTPException:
        raise
    except ModalCliError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _DIRS_CACHE.pop(vol, None)
    return {"ok": True, "path": target}


class TrashPurgeBody(BaseModel):
    paths: list[str]
    volume: str | None = None


@router.post("/trash/purge")
async def trash_purge(body: TrashPurgeBody) -> dict:
    vol = _volume_or_default(body.volume)
    results: list[dict] = []
    for raw in body.paths:
        try:
            rel = _safe_rel(raw, allow_trash=True)
            if not deletion.is_trash_path(rel):
                raise ValueError("只能永久清理 .trash/<时间戳>/... 下的条目")
            await volume_cli.remove_file(rel, volume=vol, recursive=True)
            results.append({"path": rel, "ok": True})
        except ValueError as exc:
            results.append({"path": raw, "ok": False, "error": str(exc)})
        except HTTPException as exc:
            results.append({"path": raw, "ok": False, "error": str(exc.detail)})
        except ModalCliError as exc:
            results.append({"path": raw, "ok": False, "error": str(exc)[:300]})
    usage.get_usage(force=any(r["ok"] for r in results))
    return {"ok": bool(results) and all(r["ok"] for r in results), "results": results}


async def _entry_kind(vol: str, rel: str) -> str:
    """经父目录清单判断条目类型（file/dir）；不存在时抛 ModalCliError。"""
    parent, _, name = rel.rpartition("/")
    entries = await volume_cli.list_dir(parent or "/", volume=vol)
    for e in entries:
        if e["name"] == name:
            return "dir" if e["type"] in ("dir", "directory") else "file"
    raise ModalCliError(f"路径不存在: {rel}")


class MkdirBody(BaseModel):
    path: str
    volume: str | None = None


@router.post("/mkdir")
async def mkdir(body: MkdirBody) -> dict:
    """新建文件夹（可多级）。SDK 无 mkdir：上传 .keep 标记自动建父目录后删除标记，
    留下真正的空目录（实测空目录条目会保留）；标记残留也不影响删除守卫（忽略点文件）。"""
    vol = _volume_or_default(body.volume)
    rel = _safe_rel(body.path)
    if not rel:
        raise HTTPException(status_code=422, detail="目录名不能为空")
    if any(seg.startswith(".") for seg in rel.split("/")):
        raise HTTPException(status_code=422, detail=f"目录名不允许以 . 开头: {rel!r}")
    parent, _, name = rel.rpartition("/")
    try:  # 同名预检（清单有 ~1 分钟延迟，刚创建的目录可能漏报，尽力而为）
        entries = await volume_cli.list_dir(parent or "/", volume=vol)
        if any(e["name"] == name for e in entries):
            raise HTTPException(status_code=409, detail=f"同名条目已存在: {name}")
    except ModalCliError:
        pass  # 父目录清单不可读时跳过预检，由后续 SDK 写入兜底
    with tempfile.TemporaryDirectory() as td:
        marker = Path(td) / ".keep"
        marker.write_bytes(b"")
        try:
            # force=True：父目录创建后子目录创建会重复落标记，覆盖无害（仅 0 字节点文件）
            await volume_cli.put_file(marker, f"{rel}/.keep", volume=vol, force=True)
        except ModalCliError as exc:
            raise HTTPException(status_code=409, detail=f"创建目录失败: {exc}")
    for attempt in range(2):  # 删除标记（失败重试一次；残留无害）
        try:
            await volume_cli.remove_file(f"{rel}/.keep", volume=vol)
            break
        except ModalCliError:
            if attempt == 0:
                await asyncio.sleep(1.5)
    _DIRS_CACHE.pop(vol, None)  # 新目录立即可见于目标下拉
    return {"ok": True, "path": rel, "note": "目录已创建；云端清单刷新可能有约 1 分钟延迟"}


@router.get("/dirs")
async def all_dirs(
    volume: str | None = Query(None, description="数据卷名，空 = 主卷"),
) -> dict:
    """全卷目录清单（移动目标 / 下载目标选择用）：逐层并行 BFS + 60s 服务端缓存。

    深度/数量封顶防大卷拖死；缓存键为卷名（不同卷互不影响）。
    """
    vol = _volume_or_default(volume)
    now = time.monotonic()
    cached = _DIRS_CACHE.get(vol)
    if cached and now - cached[0] < _DIRS_TTL:
        return {**cached[1], "cached": True}

    sem = asyncio.Semaphore(6)
    out: list[str] = []
    error: str | None = None

    async def expand(cur: str) -> list[str]:
        nonlocal error
        async with sem:
            try:
                entries = await volume_cli.list_dir(cur or "/", volume=vol)
            except ModalCliError as exc:
                if "No such file or directory" not in str(exc):
                    error = error or str(exc)[:200]
                return []
        children = []
        for e in entries:
            if e["type"] not in ("dir", "directory") or e["name"].startswith("."):
                continue
            child = f"{cur}/{e['name']}" if cur else e["name"]
            if len(child.split("/")) <= 6:
                children.append(child)
        return children

    try:
        queue = [""]
        while queue and len(out) < 500:
            batch, queue = queue, []
            for children in await asyncio.gather(*(expand(c) for c in batch)):
                out.extend(children)
                queue.extend(children)
    except ModalCliError as exc:
        return {"ok": False, "dirs": [], "error": str(exc)[:200], "volume": vol}
    result = {"ok": error is None, "dirs": sorted(out), "error": error, "volume": vol}
    _DIRS_CACHE[vol] = (now, result)
    return result


class MoveBody(BaseModel):
    paths: list[str]
    dest_dir: str
    volume: str | None = None


@router.post("/move")
async def move_paths(body: MoveBody) -> dict:
    """卷内移动：创建后台作业（云端容器内复制 + 删源）。创建时完成全部合法性校验。"""
    vol = _volume_or_default(body.volume)
    dest = _safe_rel(body.dest_dir)
    main = get_settings().modal.volume
    if dest:
        try:
            await volume_cli.list_dir(dest, volume=vol)
        except ModalCliError as exc:
            if "No such file or directory" in str(exc):
                raise HTTPException(status_code=422, detail=f"目标目录不存在: {dest}")
            raise HTTPException(status_code=409, detail=f"目标目录不可读: {exc}")
    try:
        dest_names = {e["name"] for e in await volume_cli.list_dir(dest or "/", volume=vol)}
    except ModalCliError:
        dest_names = set()
    seen: set[str] = set()
    entries: list[dict] = []
    for raw in body.paths:
        rel = _safe_rel(raw)
        if not rel:
            raise HTTPException(status_code=422, detail="不能移动卷根目录")
        if rel in seen:
            continue
        seen.add(rel)
        try:
            kind = await _entry_kind(vol, rel)
        except ModalCliError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        if vol == main and "/" not in rel and rel in _RESERVED_TOP:
            raise HTTPException(
                status_code=422,
                detail=f"云端结构目录 {rel} 不允许移动（ComfyUI 运行依赖）",
            )
        if kind == "dir" and (dest == rel or dest.startswith(rel + "/")):
            raise HTTPException(status_code=422, detail=f"不能把 {rel} 移动到它自身或其子目录内")
        name = rel.rsplit("/", 1)[-1]
        if name in dest_names:
            raise HTTPException(status_code=409, detail=f"目标目录已存在同名条目: {name}")
        entries.append({"src": rel, "dst": f"{dest}/{name}" if dest else name, "kind": kind})
    try:
        job = moves.create_move(vol, dest, entries)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"job": job.to_dict()}


@router.get("/moves")
async def move_jobs() -> dict:
    return {"jobs": [j.to_dict() for j in moves.list_jobs()]}


@router.post("/moves/{job_id}/cancel")
async def cancel_move(job_id: str) -> dict:
    ok = await moves.cancel_move(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="作业不存在或已结束")
    return {"cancelled": True}


class DownloadBody(BaseModel):
    url: str
    dest_dir: str
    filename: str | None = None
    volume: str | None = None


@router.post("/download")
async def create_download(body: DownloadBody) -> dict:
    vol = (body.volume or "").strip() or get_settings().modal.volume
    if not _NAME_RE.match(vol):
        raise HTTPException(status_code=422, detail=f"非法的数据卷名: {vol!r}")
    if vol != get_settings().modal.volume and not await _volume_exists(vol):
        raise HTTPException(status_code=404, detail=f"数据卷不存在: {vol}")
    try:
        job = downloads.create_download(body.url, body.dest_dir, body.filename, vol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"job": job.to_dict()}


async def _volume_exists(name: str) -> bool:
    return await volume_cli.volume_exists(name)


@router.get("/downloads")
async def download_jobs() -> dict:
    return {"jobs": [j.to_dict() for j in downloads.list_jobs()]}


@router.post("/downloads/{job_id}/cancel")
async def cancel_download(job_id: str) -> dict:
    ok = await downloads.cancel_download(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="作业不存在或已结束")
    return {"cancelled": True}

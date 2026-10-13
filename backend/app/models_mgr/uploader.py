"""模型分块上传会话：持久化分块、断点查询、MD5 校验与落卷。

上传协议把浏览器端的大文件切成固定大小的分块，后端只负责把已到达的分块
原子落盘。会话元数据存放在 ``<config_dir>/uploads/<upload_id>/meta.json``，
因此桥接器重启后仍可查询已完成分块并继续上传。

收尾流程：

1. 客户端补齐全部分块后调用 complete；
2. 后端按序合并分块并计算整文件 MD5；
3. MD5 与服务端计算值一致后，用现有 ``modal volume put`` 写入目标卷；
4. 成功后清理临时分块和合并文件，刷新云端模型索引。

本模块刻意不直接调用 Modal Python SDK：与现有卷同步、下载和移动保持一致，
所有云端写入都经 ``modal`` CLI 子进程完成。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.config import config_dir, model_dirs_resolved
from ..modal_volume import client as modal_cli
from ..models_mgr import cloud_index

_CHUNK_SIZE = 8 * 1024 * 1024
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_VOLUME_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
_JOBS_KEEP = 100

_LOCKS: dict[str, asyncio.Lock] = {}
_TASKS: dict[str, asyncio.Task] = {}
logger = logging.getLogger(__name__)


def upload_root() -> Path:
    """上传会话根目录；随 CB_CONFIG_DIR 隔离，放在版本库之外。"""
    return config_dir() / "uploads"


def _session_dir(upload_id: str) -> Path:
    if not _ID_RE.match(upload_id or ""):
        raise ValueError(f"非法的上传会话 ID: {upload_id!r}")
    return upload_root() / upload_id


def _meta_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "meta.json"


def _chunks_dir(upload_id: str) -> Path:
    return _session_dir(upload_id) / "chunks"


def _chunk_path(upload_id: str, index: int) -> Path:
    return _chunks_dir(upload_id) / f"{index:08d}.part"


def _assembled_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "assembled.bin"


def now_ms() -> int:
    return int(time.time() * 1000)


def _looks_like_active_task(upload_id: str) -> bool:
    task = _TASKS.get(upload_id)
    return task is not None and not task.done()


def _safe_filename(filename: str) -> str:
    raw = (filename or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/"):
        raise ValueError("文件名不能为空且必须是相对路径")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError(f"文件名不能包含空段、.. 或以 . 开头的段: {filename!r}")
    if any(len(part) > 255 for part in parts):
        raise ValueError("文件名单段长度不能超过 255 个字符")
    return "/".join(parts)


def _safe_dest_dir(path: str | None) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if raw.startswith("/"):
        raise ValueError(f"目标目录必须是数据卷内相对路径: {path!r}")
    raw = raw.strip("/")
    if not raw:
        return ""
    parts = raw.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError(f"目标目录不能包含空段、.. 或以 . 开头的段: {path!r}")
    return "/".join(parts)


def _validate_volume(volume: str | None) -> str:
    name = (volume or "").strip() or modal_cli.volume_name()
    if not _VOLUME_RE.match(name):
        raise ValueError(f"非法的数据卷名: {name!r}")
    return name


def _normalize_md5(value: str) -> str:
    md5 = (value or "").strip().lower()
    if not _MD5_RE.match(md5):
        raise ValueError("MD5 必须是 32 位十六进制字符串")
    return md5


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _scan_received(session: dict[str, Any]) -> None:
    """以磁盘为准刷新已收分块，避免只信 meta.json 或在崩溃后误判。"""
    if session.get("status") == "done":
        # 成功后临时分块会清理，不能再按磁盘推断“缺块”。
        session["uploaded_chunks"] = list(range(int(session["total_chunks"])))
        session["uploaded_bytes"] = int(session["size"])
        return
    upload_id = session["id"]
    total = int(session["total_chunks"])
    received: list[int] = []
    bytes_received = 0
    size = int(session["size"])
    chunk_size = int(session["chunk_size"])
    for index in range(total):
        path = _chunk_path(upload_id, index)
        if not path.is_file():
            continue
        expected = min(chunk_size, size - index * chunk_size)
        actual = path.stat().st_size
        if actual != expected:
            continue
        received.append(index)
        bytes_received += actual
    session["uploaded_chunks"] = received
    session["uploaded_bytes"] = bytes_received


def _recover_interrupted(session: dict[str, Any]) -> bool:
    """后端重启后把没有实际任务的收尾状态标成可重试错误。"""
    if session.get("status") not in {"verifying", "pushing"}:
        return False
    if _looks_like_active_task(session["id"]):
        return False
    session["status"] = "error"
    session["error_stage"] = "push"
    session["error"] = "上次收尾被中断，可继续重试"
    session["finished_ms"] = now_ms()
    session["updated_ms"] = now_ms()
    _write_json(_meta_path(session["id"]), session)
    return True


def _load(upload_id: str, *, recover: bool = True) -> dict[str, Any]:
    path = _meta_path(upload_id)
    if not path.is_file():
        raise FileNotFoundError(f"上传会话不存在: {upload_id}")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise TypeError(f"上传会话元数据损坏: {upload_id}")
    session: dict[str, Any] = data
    session.setdefault("uploaded_chunks", [])
    session.setdefault("uploaded_bytes", 0)
    _scan_received(session)
    if recover:
        _recover_interrupted(session)
    return session


def _save(session: dict[str, Any]) -> None:
    session["updated_ms"] = now_ms()
    _write_json(_meta_path(session["id"]), session)


def _session_lock(upload_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(upload_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[upload_id] = lock
    return lock


def _public(session: dict[str, Any]) -> dict[str, Any]:
    total = int(session["total_chunks"])
    received = {int(i) for i in session.get("uploaded_chunks", [])}
    missing = [i for i in range(total) if i not in received]
    return {
        "id": session["id"],
        "filename": session["filename"],
        "volume": session["volume"],
        "category": session.get("category"),
        "dest_dir": session.get("dest_dir"),
        "remote_dir": session["remote_dir"],
        "remote_path": session["remote_path"],
        "size": session["size"],
        "md5": session["md5"],
        "actual_md5": session.get("actual_md5"),
        "chunk_size": session["chunk_size"],
        "total_chunks": total,
        "uploaded_chunks": sorted(received),
        "missing_chunks": missing,
        "missing_count": len(missing),
        "uploaded_bytes": session.get("uploaded_bytes", 0),
        "ready": not missing,
        "status": session["status"],
        "error_stage": session.get("error_stage"),
        "error": session.get("error"),
        "created_ms": session["created_ms"],
        "updated_ms": session.get("updated_ms", session["created_ms"]),
        "finished_ms": session.get("finished_ms"),
    }


async def create_upload(
    *,
    filename: str,
    size: int,
    md5: str,
    category: str | None = None,
    dest_dir: str | None = None,
    volume: str | None = None,
) -> dict[str, Any]:
    safe_name = _safe_filename(filename)
    if int(size) <= 0:
        raise ValueError("文件大小必须大于 0")
    expected_md5 = _normalize_md5(md5)
    vol = _validate_volume(volume)

    if category is not None:
        category = (category or "").strip()
        if category not in model_dirs_resolved():
            raise ValueError(f"未知模型分类: {category}")
        remote_dir = f"models/{category}"
    else:
        remote_dir = _safe_dest_dir(dest_dir)
        if not remote_dir:
            raise ValueError("分块上传必须指定模型分类或目标目录")

    total = (int(size) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    if total <= 0 or total > 200_000:
        raise ValueError("分块数量超出支持范围")

    upload_id = uuid.uuid4().hex[:12]
    session: dict[str, Any] = {
        "id": upload_id,
        "filename": safe_name,
        "volume": vol,
        "category": category,
        "dest_dir": remote_dir if category is None else None,
        "remote_dir": remote_dir,
        "remote_path": f"{remote_dir}/{safe_name}",
        "size": int(size),
        "md5": expected_md5,
        "chunk_size": _CHUNK_SIZE,
        "total_chunks": total,
        "uploaded_chunks": [],
        "uploaded_bytes": 0,
        "status": "receiving",
        "error_stage": None,
        "error": None,
        "actual_md5": None,
        "created_ms": now_ms(),
        "updated_ms": now_ms(),
        "finished_ms": None,
    }
    async with _session_lock(upload_id):
        _chunks_dir(upload_id).mkdir(parents=True, exist_ok=True)
        _save(session)
    return _public(session)


def list_uploads(limit: int = _JOBS_KEEP) -> list[dict[str, Any]]:
    root = upload_root()
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in root.iterdir():
        if not path.is_dir() or not _ID_RE.match(path.name):
            continue
        try:
            session = _load(path.name)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        items.append(session)
    items.sort(key=lambda s: s.get("updated_ms", s.get("created_ms", 0)), reverse=True)
    return [_public(s) for s in items[: max(1, limit)]]


async def get_upload(upload_id: str) -> dict[str, Any]:
    async with _session_lock(upload_id):
        return _public(_load(upload_id))


async def write_chunk(
    upload_id: str,
    index: int,
    data: bytes,
    chunk_md5: str,
) -> dict[str, Any]:
    if index < 0:
        raise ValueError("分块序号不能为负数")
    expected_md5 = (chunk_md5 or "").strip().lower()
    if not _MD5_RE.match(expected_md5):
        raise ValueError("缺少或非法的分块 MD5（x-chunk-md5）")

    async with _session_lock(upload_id):
        session = _load(upload_id)
        if session.get("status") == "done":
            raise ValueError("上传会话已完成，不能继续写分块")
        if session.get("status") in {"verifying", "pushing"} and _looks_like_active_task(upload_id):
            raise ValueError("上传会话正在收尾，不能写分块")
        total = int(session["total_chunks"])
        if index >= total:
            raise ValueError(f"分块序号超出范围: {index}")
        expected_size = min(int(session["chunk_size"]), int(session["size"]) - index * int(session["chunk_size"]))
        if len(data) != expected_size:
            raise ValueError(f"分块大小不匹配：期望 {expected_size} 字节，收到 {len(data)} 字节")
        actual_md5 = hashlib.md5(data).hexdigest()
        if actual_md5 != expected_md5:
            raise ValueError(f"分块 MD5 不匹配：期望 {expected_md5}，实际 {actual_md5}")

        _atomic_write(_chunk_path(upload_id, index), data)
        _scan_received(session)
        session["status"] = "receiving"
        session["error"] = None
        session["error_stage"] = None
        session["finished_ms"] = None
        _save(session)
        return _public(session)


def _merge_and_hash(upload_id: str, session: dict[str, Any]) -> str:
    """按序合并分块并返回整文件 MD5；合并文件先写临时文件再原子替换。"""
    expected_size = int(session["size"])
    expected_md5 = str(session["md5"]).lower()
    assembled = _assembled_path(upload_id)

    if assembled.is_file() and assembled.stat().st_size == expected_size:
        digest = hashlib.md5()
        with assembled.open("rb") as src:
            for block in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() == expected_md5:
            return digest.hexdigest()

    tmp = assembled.with_name(f".{assembled.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.md5()
    try:
        with tmp.open("wb") as dst:
            for index in range(int(session["total_chunks"])):
                chunk = _chunk_path(upload_id, index)
                if not chunk.is_file():
                    raise ValueError(f"缺少分块 {index}，无法合并")
                with chunk.open("rb") as src:
                    for block in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(block)
                        digest.update(block)
        if tmp.stat().st_size != expected_size:
            raise ValueError(f"合并文件大小不匹配：期望 {expected_size} 字节，实际 {tmp.stat().st_size} 字节")
        os.replace(tmp, assembled)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return digest.hexdigest()


async def _update(upload_id: str, **fields: Any) -> dict[str, Any]:
    async with _session_lock(upload_id):
        session = _load(upload_id, recover=False)
        session.update(fields)
        _save(session)
        return _public(session)


async def _finalize(upload_id: str) -> None:
    try:
        async with _session_lock(upload_id):
            session = _load(upload_id, recover=False)
            session["status"] = "verifying"
            session["error"] = None
            session["error_stage"] = None
            _save(session)
            session_snapshot = dict(session)

        actual_md5 = await asyncio.to_thread(_merge_and_hash, upload_id, session_snapshot)
        if actual_md5.lower() != str(session_snapshot["md5"]).lower():
            _assembled_path(upload_id).unlink(missing_ok=True)
            await _update(
                upload_id,
                status="error",
                error_stage="verify",
                error=f"整文件 MD5 不匹配：期望 {session_snapshot['md5']}，实际 {actual_md5}",
                actual_md5=actual_md5,
                finished_ms=now_ms(),
            )
            return

        await _update(
            upload_id,
            status="pushing",
            actual_md5=actual_md5,
            error=None,
            error_stage=None,
        )
        await modal_cli.run(
            "volume",
            "put",
            str(session_snapshot["volume"]),
            str(_assembled_path(upload_id)),
            str(session_snapshot["remote_path"]),
            "-f",
            timeout=24 * 3600,
        )
        await _update(
            upload_id,
            status="done",
            error=None,
            error_stage=None,
            finished_ms=now_ms(),
        )
        try:
            await cloud_index.refresh(force=True)
        except Exception as exc:  # noqa: BLE001 —— 落卷已成功，索引刷新失败不改变上传结果
            logger.warning("上传完成后云端模型索引刷新失败: %s", exc)
        await asyncio.to_thread(_cleanup_payload, upload_id)
    except Exception as exc:  # noqa: BLE001 —— 后台任务必须把失败写回会话
        await _update(
            upload_id,
            status="error",
            error_stage="push" if _assembled_path(upload_id).is_file() else "verify",
            error=str(exc)[:400],
            finished_ms=now_ms(),
        )


def _cleanup_payload(upload_id: str) -> None:
    shutil.rmtree(_chunks_dir(upload_id), ignore_errors=True)
    _assembled_path(upload_id).unlink(missing_ok=True)


async def complete_upload(upload_id: str) -> dict[str, Any]:
    async with _session_lock(upload_id):
        session = _load(upload_id)
        if session.get("status") == "done":
            return _public(session)
        if session.get("status") == "error" and session.get("error_stage") == "verify":
            raise ValueError("MD5 校验失败，请删除会话并重新上传文件")
        if session.get("status") in {"verifying", "pushing"} and _looks_like_active_task(upload_id):
            return _public(session)
        received = {int(i) for i in session.get("uploaded_chunks", [])}
        missing = [i for i in range(int(session["total_chunks"])) if i not in received]
        if missing:
            preview = ", ".join(str(i) for i in missing[:20])
            raise ValueError(f"仍有 {len(missing)} 个分块未上传：{preview}")
        session["status"] = "verifying"
        session["error"] = None
        session["error_stage"] = None
        session["finished_ms"] = None
        _save(session)

        task = _TASKS.get(upload_id)
        if task is None or task.done():
            task = asyncio.create_task(_finalize(upload_id))
            _TASKS[upload_id] = task
            task.add_done_callback(lambda _t: _TASKS.pop(upload_id, None))
        return _public(_load(upload_id, recover=False))


async def delete_upload(upload_id: str) -> bool:
    async with _session_lock(upload_id):
        path = _session_dir(upload_id)
        if not path.is_dir():
            return False
        task = _TASKS.get(upload_id)
        if task is not None and not task.done():
            task.cancel()
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
    return True

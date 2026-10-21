"""用户数据保留、备份与恢复。

备份是运行中可安全执行的在线快照：SQLite 通过 backup API 复制，配置和上传
元数据按文件复制。恢复不在进程运行中替换数据库，而是先校验并暂存，由下一次
启动在打开任何数据库连接前应用。该边界避免 Windows 文件锁和 WAL 半写状态。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .config import CONFIG_PATH, config_dir

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
RESTORE_STAGING_NAME = "restore-staging.zip"
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650
_MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_BACKUP_KEEP = 10
_LOCK = threading.RLock()
_PROCESS_LOCK_TIMEOUT = 30.0


class BackupError(ValueError):
    """备份或恢复请求不满足安全边界。"""


class RestoreBusyError(BackupError):
    """另一个进程仍持有恢复锁；调用方不得继续打开用户数据。"""


def _restore_lock_path() -> Path:
    return config_dir() / ".restore.lock"


def _acquire_file_lock(stream: BinaryIO, *, timeout: float) -> None:
    """获取跨进程文件锁；超时后明确失败，不降级为无锁运行。"""
    deadline = time.monotonic() + max(0.0, timeout)
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        while True:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise RestoreBusyError(
                        "另一个 ComfyBridge 实例正在校验或恢复数据，"
                        "请等待其完成后再启动；本次未修改任何数据。"
                    ) from None
                time.sleep(0.1)

    import fcntl

    while True:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RestoreBusyError(
                    "另一个 ComfyBridge 实例正在校验或恢复数据，"
                    "请等待其完成后再启动；本次未修改任何数据。"
                ) from None
            time.sleep(0.1)


def _release_file_lock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def _process_restore_lock(*, timeout: float = _PROCESS_LOCK_TIMEOUT) -> Iterator[None]:
    """同一用户配置目录的恢复事务必须跨进程串行。"""
    path = _restore_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        _acquire_file_lock(stream, timeout=timeout)
        try:
            yield
        finally:
            _release_file_lock(stream)


def error_guidance(exc: BaseException) -> str:
    """把系统错误转换成用户可执行的中文建议，同时保留原始诊断。"""
    detail = str(exc).strip()
    if isinstance(exc, PermissionError):
        return (
            f"没有权限访问数据或备份目录：{detail}。"
            "请关闭占用文件的程序、检查目录权限后重试；不要删除当前数据目录。"
        )
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {39, 112}:
        return (
            "磁盘空间不足。请清理下载目录、旧备份或回收站后重试；"
            "当前数据未被删除。"
        )
    if isinstance(exc, sqlite3.DatabaseError):
        return (
            f"请求数据库已损坏或无法读取：{detail}。"
            "请从「数据安全」选择一份已验证备份恢复；恢复会在下次启动应用。"
        )
    return f"操作失败：{detail}"


def data_dir() -> Path:
    """请求库目录；延迟解析，测试可替换日志存储的 DATA_DIR。"""
    from ..logs import store

    return Path(store.DATA_DIR)


def db_path() -> Path:
    from ..logs import store

    return Path(store.DB_PATH)


def backup_dir() -> Path:
    """备份目录放在用户配置目录，避免升级替换整合包时丢失。"""
    return config_dir() / "backups"


def restore_marker() -> Path:
    return config_dir() / "restore-applied.json"


def retention_settings_path() -> Path:
    return config_dir() / "retention.json"


def coalesce_paths(*paths: Path) -> list[Path]:
    """去重同一文件/目录；已存在父目录时不再重复添加子路径。"""
    seen: list[Path] = []
    for path in paths:
        resolved = Path(path)
        if not resolved.exists():
            continue
        if any(resolved == old or old in resolved.parents for old in seen):
            continue
        seen = [old for old in seen if resolved not in old.parents]
        seen.append(resolved)
    return seen


def readable_retention() -> dict[str, Any]:
    """保留期配置损坏时回退默认值，保证维护接口仍可打开。"""
    path = retention_settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        days = int(raw.get("days", DEFAULT_RETENTION_DAYS))
    except (OSError, ValueError, TypeError, AttributeError):
        days = DEFAULT_RETENTION_DAYS
    days = max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))
    return {"days": days}


def save_retention(days: int) -> dict[str, Any]:
    if not MIN_RETENTION_DAYS <= int(days) <= MAX_RETENTION_DAYS:
        raise BackupError(
            f"保留天数必须在 {MIN_RETENTION_DAYS}-{MAX_RETENTION_DAYS} 之间"
        )
    value = {"days": int(days), "updated_ms": int(time.time() * 1000)}
    _atomic_json(retention_settings_path(), value)
    return {"days": int(days)}


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(src: Path, dst: Path) -> int:
    """复制普通用户数据；跳过缓存、符号链接和临时原子写残留。"""
    if not src.is_dir():
        return 0
    count = 0
    for item in src.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        rel = item.relative_to(src)
        if any(part == "__pycache__" or part.startswith(".") for part in rel.parts):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def _sqlite_snapshot(dst: Path) -> None:
    from ..logs import store

    dst.parent.mkdir(parents=True, exist_ok=True)

    def copy_under_lock() -> None:
        # 在线 backup 与 schema 提交串行。启动升级时尚未初始化连接，必须直接
        # 打开现有数据库做快照；调用 _get_conn() 会先建表/补列，越过迁移前备份边界。
        with store._lock:
            database = Path(store.DB_PATH)
            owns_source = store._conn is None
            if store._conn is not None:
                src = store._conn
            elif database.is_file():
                src = sqlite3.connect(
                    f"file:{database.as_posix()}?mode=rw",
                    uri=True,
                    check_same_thread=False,
                )
            else:
                # 全新安装尚无请求库；创建带 schema 的空库，满足备份格式约束。
                src = store._get_conn()
                owns_source = False
            out = sqlite3.connect(dst)
            try:
                src.backup(out)
                out.commit()
            finally:
                out.close()
                if owns_source:
                    src.close()

    # 管理 API 在线程池中执行；并发备份各用独立单线程，避免 SQLite 跨线程取连接。
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cb-backup")
    try:
        executor.submit(copy_under_lock).result()
    finally:
        executor.shutdown(wait=True)


def create_backup(*, reason: str = "manual") -> dict[str, Any]:
    """创建 zip 并返回可安全展示的元数据；备份内含凭证，下载须管理员认证。"""
    with _LOCK:
        home = config_dir()
        target_dir = backup_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        backup_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        output = target_dir / f"ComfyBridge-backup-{backup_id}.zip"
        partial = output.with_suffix(".zip.partial")
        # Windows 上系统 tempfile 目录可能继承异常 ACL；工作目录显式建在备份根下。
        tmpdir = target_dir / f".cb-backup-{uuid.uuid4().hex}"
        tmpdir.mkdir()
        try:
            payload = tmpdir / "payload"
            payload.mkdir()
            files: list[dict[str, Any]] = []

            def add(path: Path, arcname: str) -> None:
                files.append({
                    "path": arcname,
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                })

            database = payload / "bridge.sqlite3"
            _sqlite_snapshot(database)
            database = payload / "data" / "bridge.sqlite3"
            database.parent.mkdir(parents=True, exist_ok=True)
            os.replace(payload / "bridge.sqlite3", database)
            add(database, "data/bridge.sqlite3")

            graph_src = data_dir() / "graphs"
            graph_files = _copy_tree(graph_src, payload / "data" / "graphs")
            for item in (payload / "data" / "graphs").rglob("*"):
                if item.is_file():
                    add(item, item.relative_to(payload).as_posix())

            upload_files = _copy_tree(home / "uploads", payload / "uploads")
            for item in (payload / "uploads").rglob("*"):
                if item.is_file():
                    add(item, item.relative_to(payload).as_posix())

            if CONFIG_PATH.is_file():
                cfg = payload / "config.toml"
                cfg.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(CONFIG_PATH, cfg)
                add(cfg, "config.toml")
            audit = home / "audit.log"
            if audit.is_file():
                audit_dst = payload / "audit.log"
                shutil.copy2(audit, audit_dst)
                add(audit_dst, "audit.log")

            manifest = {
                "format": 1,
                "app": "ComfyBridge",
                "created_ms": int(time.time() * 1000),
                "reason": reason[:80],
                "graph_files": graph_files,
                "upload_files": upload_files,
                "files": files,
            }
            manifest_path = tmpdir / MANIFEST_NAME
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(manifest_path, MANIFEST_NAME)
                for file in sorted(payload.rglob("*")):
                    if file.is_file():
                        arcname = file.relative_to(payload).as_posix()
                        # ZipFile.write 对相对路径的目录项行为在不同 Python 版本
                        # 不一致；显式写入文件内容，保证清单与包内路径一致。
                        zf.write(file, arcname)
            os.replace(partial, output)
        except Exception:
            partial.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        backups = sorted(
            target_dir.glob("ComfyBridge-backup-*.zip"),
            key=lambda item: (item.stat().st_mtime_ns, item.name),
            reverse=True,
        )
        for stale in backups[_BACKUP_KEEP:]:
            stale.unlink(missing_ok=True)
        return {
            "id": backup_id,
            "name": output.name,
            "path": str(output),
            "size": output.stat().st_size,
            "created_ms": int(time.time() * 1000),
            "reason": reason,
            "files": len(files),
            "contains_secrets": True,
        }


def list_backups() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = backup_dir()
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("ComfyBridge-backup-*.zip"), reverse=True):
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append({
            "name": path.name,
            "size": st.st_size,
            "created_ms": int(st.st_mtime * 1000),
            "reason": "unknown",
        })
    return rows


def status() -> dict[str, Any]:
    """汇总设置页所需状态；任何损坏文件都不会让整个页面不可用。"""
    marker: dict[str, Any] | None = None
    marker_path = restore_marker()
    if marker_path.is_file():
        try:
            raw = json.loads(marker_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                marker = raw
        except (OSError, ValueError):
            marker = None
    return {
        "retention": readable_retention(),
        "backups": list_backups(),
        "restore_pending": (config_dir() / RESTORE_STAGING_NAME).is_file(),
        "last_restore": marker,
        "data_dir": str(data_dir()),
        "backup_dir": str(backup_dir()),
    }


def resolve_backup(name: str) -> Path:
    """只允许下载备份目录中的规范文件名，阻断路径穿越。"""
    if not name or Path(name).name != name or not name.endswith(".zip"):
        raise BackupError("非法备份文件名")
    path = (backup_dir() / name).resolve()
    root = backup_dir().resolve()
    if path.parent != root or not path.is_file():
        raise FileNotFoundError(name)
    return path


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(p.parts) and not p.is_absolute() and ".." not in p.parts and "\\" not in name


def _remove_path(path: Path) -> None:
    """删除恢复事务中的一个文件或目录；不跟随符号链接。"""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _row_size(row: dict[str, Any]) -> int:
    value = row.get("size")
    if isinstance(value, bool):
        raise BackupError("备份清单包含非法文件大小")
    if not isinstance(value, (int, str)):
        raise BackupError("备份清单包含非法文件大小")
    try:
        size = int(value)
    except ValueError as exc:
        raise BackupError("备份清单包含非法文件大小") from exc
    if size < 0:
        raise BackupError("备份清单包含非法文件大小")
    return size


def validate_backup(path: Path) -> dict[str, Any]:
    """校验结构、成员白名单和文件哈希；数据库另做 quick_check。"""
    if path.stat().st_size > _MAX_BACKUP_BYTES:
        raise BackupError("备份文件超过 2 GiB 限制")
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise BackupError(f"备份压缩包损坏：{bad}")
            infos = zf.infolist()
            member_names = [info.filename for info in infos]
            if len(member_names) != len(set(member_names)):
                raise BackupError("备份包含重复成员")
            if any(info.is_dir() or info.flag_bits & 0x1 for info in infos):
                raise BackupError("备份包含不支持的目录或加密成员")
            if sum(info.file_size for info in infos) > _MAX_BACKUP_BYTES:
                raise BackupError("备份解压后超过 2 GiB 限制")
            names = set(member_names)
            if MANIFEST_NAME not in member_names:
                raise BackupError("备份缺少 manifest.json")
            info = zf.getinfo(MANIFEST_NAME)
            if info.file_size > _MAX_MANIFEST_BYTES:
                raise BackupError("备份 manifest 过大")
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            if manifest.get("app") != "ComfyBridge" or int(manifest.get("format", 0)) != 1:
                raise BackupError("不是 ComfyBridge 支持的备份格式")
            files = manifest.get("files")
            if not isinstance(files, list) or not files:
                raise BackupError("备份 manifest 缺少文件清单")
            listed: set[str] = set()
            for row in files:
                if not isinstance(row, dict):
                    raise BackupError("备份 manifest 文件项格式错误")
                name = str(row.get("path", ""))
                if not _safe_member(name) or name not in names:
                    raise BackupError(f"备份路径缺失或不安全：{name!r}")
                if name in listed:
                    raise BackupError(f"备份路径重复：{name!r}")
                listed.add(name)
                if _row_size(row) != zf.getinfo(name).file_size:
                    raise BackupError(f"备份文件大小不一致：{name}")
                digest = hashlib.sha256()
                with zf.open(name) as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != row.get("sha256"):
                    raise BackupError(f"备份文件哈希不一致：{name}")
            expected = listed | {MANIFEST_NAME}
            if names != expected:
                extra = sorted(names - expected)
                missing = sorted(expected - names)
                detail = extra[0] if extra else missing[0]
                raise BackupError(f"备份成员与清单不一致：{detail!r}")
            if "data/bridge.sqlite3" not in listed:
                raise BackupError("备份缺少请求数据库")
            check_root = path.parent / f".cb-check-{uuid.uuid4().hex}"
            check_root.mkdir()
            try:
                db = check_root / "bridge.sqlite3"
                db.write_bytes(zf.read("data/bridge.sqlite3"))
                conn = sqlite3.connect(db)
                try:
                    state = conn.execute("PRAGMA quick_check").fetchone()
                    tables = {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                finally:
                    conn.close()
                if not state or state[0] != "ok":
                    raise BackupError("备份数据库完整性检查失败")
                if "requests" not in tables:
                    raise BackupError("备份数据库缺少 requests 表")
            finally:
                shutil.rmtree(check_root, ignore_errors=True)
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        json.JSONDecodeError,
        UnicodeDecodeError,
        RuntimeError,
    ) as exc:
        raise BackupError(f"备份无法解析：{exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise BackupError(error_guidance(exc)) from exc
    created = manifest.get("created_ms")
    try:
        created_ms = int(created) if created is not None else 0
    except (TypeError, ValueError):
        created_ms = 0
    return {
        "name": path.name,
        "created_ms": created_ms,
        "reason": str(manifest.get("reason") or "unknown"),
        "files": len(files),
        "size": path.stat().st_size,
    }


def stage_restore(source: Path) -> dict[str, Any]:
    """校验上传文件并原子暂存；真正应用发生在下一次后端启动。"""
    if source.stat().st_size > _MAX_BACKUP_BYTES:
        raise BackupError("恢复文件超过 2 GiB 限制")
    info = validate_backup(source)
    with _process_restore_lock():
        staging = config_dir() / RESTORE_STAGING_NAME
        staging.parent.mkdir(parents=True, exist_ok=True)
        tmp = staging.with_name(f".{staging.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(source, tmp)
        os.replace(tmp, staging)
        # 恢复会替换用户数据；必须成功留下当前状态后再进入待恢复队列。
        try:
            create_backup(reason="automatic_before_restore")
        except Exception as exc:
            staging.unlink(missing_ok=True)
            raise BackupError(f"恢复前自动备份失败，未暂存恢复：{exc}") from exc
    return {**info, "pending": True, "applies_on": "next_startup"}


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _copy_resource(source: Path | None, target: Path, *, directory: bool) -> None:
    """在目标同卷建立恢复暂存，避免跨盘 os.replace。"""
    if directory:
        if source is not None and source.is_dir():
            shutil.copytree(source, target)
        else:
            target.mkdir()
        return
    if source is not None and source.is_file():
        shutil.copy2(source, target)


def _commit_restore_swaps(
    swaps: list[tuple[Path | None, Path, Path]],
) -> None:
    """按顺序原子切换资源；任一失败时逆序回滚已切换资源。"""
    committed: list[tuple[Path | None, Path, Path]] = []
    for source, target, previous in swaps:
        target.parent.mkdir(parents=True, exist_ok=True)
        if _path_exists(previous):
            _remove_path(previous)
        old_moved = False
        try:
            if _path_exists(target):
                os.replace(target, previous)
                old_moved = True
            if source is not None:
                os.replace(source, target)
        except Exception as swap_exc:
            current_error: Exception | None = None
            if old_moved and not _path_exists(target) and _path_exists(previous):
                try:
                    os.replace(previous, target)
                except Exception as exc:  # noqa: BLE001
                    current_error = exc
            rollback_errors: list[str] = []
            for _, done_target, done_previous in reversed(committed):
                try:
                    if _path_exists(done_target):
                        _remove_path(done_target)
                    if _path_exists(done_previous):
                        os.replace(done_previous, done_target)
                except Exception as exc:  # noqa: BLE001
                    rollback_errors.append(f"{done_target}: {exc}")
            if current_error is not None:
                rollback_errors.insert(0, f"{target}: {current_error}")
            suffix = (
                f"；回滚不完整，旧数据保留在 {previous}"
                if rollback_errors
                else "；已回滚本次恢复"
            )
            raise BackupError(f"恢复替换失败：{swap_exc}{suffix}") from swap_exc
        committed.append((source, target, previous))


def apply_pending_restore() -> dict[str, Any] | None:
    """启动早期应用暂存备份；成功前不清理当前数据。"""
    staging = config_dir() / RESTORE_STAGING_NAME
    if not staging.is_file():
        return None
    with _process_restore_lock():
        # 等锁期间另一个进程可能已完成恢复并移走固定暂存名。
        if not staging.is_file():
            return None
        try:
            info = validate_backup(staging)
        except Exception as exc:
            logger.error("暂存备份校验失败，保留原数据：%s", exc)
            raise

        return _apply_pending_restore_locked(staging, info)


def _apply_pending_restore_locked(staging: Path, info: dict[str, Any]) -> dict[str, Any]:
    """调用方持有跨进程锁；线程锁在此处串行在线备份等内部状态。"""
    with _LOCK:
        home = config_dir()
        from ..logs import store
        # 生产启动时此处通常尚未 init_db；测试或热调用可能已有连接句柄，
        # 先走统一的 close_db 释放 WAL/SHM，再进行文件替换。
        store.close_db()
        from . import job_store
        job_store.close_default()
        database = db_path()
        database.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        restore_input = home / f".restore-input-{token}.zip"
        # 先原子移出固定暂存名：即使后续清理失败，下一次启动也不会重复应用。
        os.replace(staging, restore_input)
        # 暂存阶段已留下安全副本；此处不重新打开 SQLite，避免 Windows 文件锁。
        work = home / f".cb-restore-{token}"
        work.mkdir()
        stage_paths: list[Path] = []
        old_paths: list[Path] = []
        committed = False
        try:
            with zipfile.ZipFile(restore_input) as zf:
                for member in zf.infolist():
                    name = member.filename
                    if not _safe_member(name):
                        raise BackupError(f"备份包含不安全路径：{name!r}")
                    dest = work / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)

            graph_dir = data_dir() / "graphs"
            upload_root = home / "uploads"
            graph_stage = data_dir() / f".graphs.restore-{token}"
            upload_stage = home / f".uploads.restore-{token}"
            config_stage = home / f".config.toml.restore-{token}"
            audit_stage = home / f".audit.log.restore-{token}"
            db_stage = database.with_name(f".{database.name}.restore-{token}")
            stage_paths.extend([graph_stage, upload_stage, config_stage, audit_stage, db_stage])
            _copy_resource(work / "data" / "graphs", graph_stage, directory=True)
            _copy_resource(work / "uploads", upload_stage, directory=True)
            _copy_resource(work / "config.toml", config_stage, directory=False)
            _copy_resource(work / "audit.log", audit_stage, directory=False)
            _copy_resource(
                work / "data" / "bridge.sqlite3", db_stage, directory=False
            )
            if not db_stage.is_file():
                raise BackupError("恢复暂存缺少请求数据库")

            graph_old = data_dir() / f".graphs.pre-restore-{token}"
            upload_old = home / f".uploads.pre-restore-{token}"
            config_old = home / f".config.toml.pre-restore-{token}"
            audit_old = home / f".audit.log.pre-restore-{token}"
            db_old = database.with_name(f"{database.name}.pre-restore")
            old_paths.extend([graph_old, upload_old, config_old, audit_old, db_old])
            _commit_restore_swaps([
                (graph_stage, graph_dir, graph_old),
                (upload_stage, upload_root, upload_old),
                (
                    config_stage if config_stage.is_file() else None,
                    CONFIG_PATH,
                    config_old,
                ),
                (
                    audit_stage if audit_stage.is_file() else None,
                    home / "audit.log",
                    audit_old,
                ),
                (db_stage, database, db_old),
            ])
            committed = True

            try:
                _atomic_json(restore_marker(), {
                    "applied_ms": int(time.time() * 1000),
                    "source": info["name"],
                })
            except OSError:
                logger.warning("恢复已完成，但恢复标记写入失败", exc_info=True)
            return {"ok": True, **info}
        finally:
            for path in stage_paths:
                if _path_exists(path):
                    _remove_path(path)
            for path in old_paths:
                # 失败时保留未回滚的旧数据；成功后除数据库回滚副本外均清理。
                if committed and _path_exists(path) and path != database.with_name(
                    f"{database.name}.pre-restore"
                ):
                    _remove_path(path)
            if committed:
                restore_input.unlink(missing_ok=True)
            elif restore_input.is_file() and not staging.exists():
                # 替换和回滚均完成但恢复未成功时，保留待恢复包供用户修正后重试。
                try:
                    os.replace(restore_input, staging)
                except OSError:
                    logger.warning("恢复失败，暂时无法归还暂存文件", exc_info=True)
            shutil.rmtree(work, ignore_errors=True)


def cleanup_retention(*, days: int | None = None) -> dict[str, Any]:
    """清理过期终态数据与孤立图快照；不删除上传会话、配置或用户产物。"""
    retention = readable_retention()
    chosen = retention["days"] if days is None else int(days)
    if not MIN_RETENTION_DAYS <= chosen <= MAX_RETENTION_DAYS:
        raise BackupError(
            f"保留天数必须在 {MIN_RETENTION_DAYS}-{MAX_RETENTION_DAYS} 之间"
        )
    cutoff_ms = int((time.time() - chosen * 86400) * 1000)
    from ..logs import store

    with store._lock:
        conn = store._get_conn()
        requests = conn.execute(
            "DELETE FROM requests WHERE created_ms < ? "
            "AND status NOT IN ('queued', 'running')",
            (cutoff_ms,),
        ).rowcount
        conn.commit()
        graph_names = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT graph_file FROM requests WHERE graph_file IS NOT NULL "
            )
        }
        try:
            jobs = conn.execute(
                "DELETE FROM jobs WHERE created_ms < ? "
                "AND json_extract(payload, '$.state') NOT IN ('queued', 'running')",
                (cutoff_ms,),
            ).rowcount
        except sqlite3.OperationalError:
            # 极旧 SQLite 没有 JSON1；作业表尚未建立时也不能阻塞请求表清理。
            jobs = 0
        conn.commit()

    graphs = 0
    for path in (data_dir() / "graphs").glob("*.json"):
        # 文件可能被任意年龄的请求引用；只有已无任何记录引用的快照才可删。
        if path.name not in graph_names and path.stat().st_mtime * 1000 < cutoff_ms:
            try:
                path.unlink()
                graphs += 1
            except OSError:
                pass
    return {
        "ok": True,
        "days": chosen,
        "cutoff_ms": cutoff_ms,
        "requests_removed": max(0, requests),
        "jobs_removed": max(0, jobs),
        "graphs_removed": graphs,
    }

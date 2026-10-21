"""本机 ComfyUI 安装扫描（设置 → 本地「扫描本机」）。

单槽后台线程：枚举固定/可移动磁盘（按要求排除 C 盘），BFS 按目录特征
「main.py + comfy/ + comfy_extras/」识别 ComfyUI 仓库根；命中后不再下钻
（其下 models 等目录庞大且无意义）。设总时限 / 目录数 / 命中数上限防失控，
超限如实标记 truncated。任务快照节流写入 JobStore；后端重启后遗留运行态
收敛为 interrupted，不自动重扫。
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .job_store import SQLiteJobStore, default_store

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
EXCLUDED_LETTERS = {"c"}     # 用户明确要求：扫描排除 C 盘
DEPTH_LIMIT = 9              # 距盘根的最大下钻层数（实际安装可能嵌在很深的工程目录里）
DIR_LIMIT = 150_000          # 目录枚举总量上限（防极端大盘拖死）
FIND_LIMIT = 30              # 命中数上限
TIME_LIMIT = 180.0           # 扫描总时限（秒）
_NAMESPACE = "comfy_scan"
_PERSIST_DIR_EVERY = 250     # 扫描进度节流写库，避免每枚举一个目录都提交 SQLite
_TERMINAL_STATES = {"done", "error", "interrupted"}

_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
# 完整性判据：必需项缺失 = 不是可用的 ComfyUI 仓库根；可选项缺失仅提示
_REQUIRED = [
    ("main.py", "main.py"),
    ("nodes.py", "nodes.py"),
    ("execution.py", "execution.py"),
    ("folder_paths.py", "folder_paths.py"),
    ("comfy", "comfy/"),
    ("comfy_extras", "comfy_extras/"),
]
_OPTIONAL = [
    ("models", "models/"),
    ("custom_nodes", "custom_nodes/"),
    ("input", "input/"),
    ("output", "output/"),
    ("user", "user/"),
    ("comfyui_version.py", "comfyui_version.py"),
    ("requirements.txt", "requirements.txt"),
]
# 盘根附近成体量的系统 / 依赖目录直接跳过（大小写不敏感）
_DIR_SKIP = {
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", "appdata", "perflogs", "recovery",
    "node_modules", "__pycache__", ".git", ".venv", "venv", "site-packages",
    "drivers", "nvidia", "intel", "amd", "msocache",
}

_LOCK = threading.Lock()
_CANCEL = threading.Event()
_JOB_ID: str | None = None
_LAST_PERSIST_DIRS = 0
_STATE: dict = {
    "id": None,
    "status": "idle",          # idle | running | done | error | interrupted
    "created_ms": None,
    "drives": [],              # 本次扫描的盘（如 ["E:\\", "F:\\"]）
    "excluded": [],            # 明确排除的盘（["C:"]）
    "current_drive": None,
    "scanned_dirs": 0,
    "found": [],               # [{path, drive, version, has_venv}]
    "truncated": False,
    "cancelled": False,
    "error": None,
    "started_ms": None,
    "finished_ms": None,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store():
    store = default_store()
    if isinstance(store, SQLiteJobStore):
        store.set_terminal_predicate(
            lambda job: job.get("status") in _TERMINAL_STATES,
            namespace=_NAMESPACE,
        )
    return store


def _copy_state(source: dict | None = None) -> dict:
    state = source or _STATE
    return {
        **state,
        "drives": list(state.get("drives") or []),
        "excluded": list(state.get("excluded") or []),
        "found": [
            {
                **item,
                "optional_missing": list(item.get("optional_missing") or []),
            }
            for item in state.get("found") or []
        ],
    }


def _latest_job() -> dict | None:
    jobs = _store().list(_NAMESPACE)
    if not jobs:
        return None
    return max(jobs, key=lambda job: float(job.get("created_ms") or 0))


def _save_locked(*, force: bool = False) -> None:
    """锁内保存当前快照；默认按目录数节流，最后一帧必须 force。"""
    global _LAST_PERSIST_DIRS
    if _JOB_ID is None:
        return
    scanned = int(_STATE.get("scanned_dirs") or 0)
    if not force and scanned - _LAST_PERSIST_DIRS < _PERSIST_DIR_EVERY:
        return
    stored = _store().save(_NAMESPACE, _JOB_ID, _copy_state())
    if isinstance(stored, dict):
        _STATE.update(_copy_state(stored))
    _LAST_PERSIST_DIRS = scanned


def state() -> dict:
    global _JOB_ID
    with _LOCK:
        if _JOB_ID is None:
            latest = _latest_job()
            if latest is not None:
                _JOB_ID = str(latest.get("id") or "") or None
                _STATE.update(_copy_state(latest))
        return _copy_state()


def start() -> None:
    """启动一次扫描；已有扫描在跑时抛 RuntimeError（API 层转 409）。"""
    global _JOB_ID, _LAST_PERSIST_DIRS
    with _LOCK:
        if _STATE["status"] == "running":
            raise RuntimeError("扫描已在进行中")
        _JOB_ID = uuid.uuid4().hex[:12]
        _CANCEL.clear()
        now = _now_ms()
        _STATE.update(
            id=_JOB_ID, status="running", created_ms=now,
            drives=[], excluded=[], current_drive=None,
            scanned_dirs=0, found=[], truncated=False, cancelled=False,
            error=None, started_ms=now, finished_ms=None,
        )
        _LAST_PERSIST_DIRS = 0
        _store().create(_NAMESPACE, _JOB_ID, _copy_state())
    threading.Thread(target=_thread_body, name="comfy-scan", daemon=True).start()


def cancel() -> None:
    _CANCEL.set()


# ---------------------------------------------------------------- 磁盘枚举
def _enumerate_drives() -> tuple[list[str], list[str]]:
    """固定/可移动磁盘列表 + 被排除盘列表（排除 C 盘；非 Windows 回退根目录）。"""
    if os.name != "nt":
        return [os.sep], []
    scan, excluded = [], []
    for i in range(26):
        letter = chr(ord("A") + i)
        root = f"{letter}:\\"
        dtype = None
        with contextlib.suppress(Exception):  # API 不可用视为盘不存在
            dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if dtype not in (DRIVE_FIXED, DRIVE_REMOVABLE):
            continue
        if letter.lower() in EXCLUDED_LETTERS:
            excluded.append(f"{letter}:")
            continue
        scan.append(root)
    return scan, excluded


# ---------------------------------------------------------------- 特征识别
def _read_version(path: Path) -> str | None:
    try:
        head = (path / "comfyui_version.py").read_text(encoding="utf-8", errors="replace")[:400]
        m = _VERSION_RE.search(head)
        return m.group(1) if m else None
    except OSError:
        return None


def _identify(path: Path) -> str | None:
    """是 ComfyUI 仓库根则返回版本号（可能为 None），否则返回 None。"""
    try:
        if not ((path / "main.py").is_file()
                and (path / "comfy").is_dir()
                and (path / "comfy_extras").is_dir()):
            return None
    except OSError:
        return None
    return _read_version(path) or ""


def _has_venv(path: Path) -> bool:
    for name in (".venv", "venv"):
        if (path / name / "Scripts" / "python.exe").is_file():
            return True
    return False


def inspect_root(path_str: str) -> dict:
    """校验一个目录是否为完整 ComfyUI 仓库根，返回逐项完整性报告。

    valid = 必需项齐全；required_missing / optional_missing 供 API/UI 展示。
    """
    path = Path(path_str)
    result: dict = {
        "path": str(path),
        "exists": path.is_dir(),
        "version": None,
        "has_venv": False,
        "required_missing": [],
        "optional_missing": [],
        "valid": False,
    }
    if not result["exists"]:
        return result

    def missing(rel: str) -> bool:
        target = path / rel
        if target.is_file():
            return False
        try:
            return not target.is_dir()
        except OSError:
            return True

    result["required_missing"] = [label for rel, label in _REQUIRED if missing(rel)]
    result["optional_missing"] = [label for rel, label in _OPTIONAL if missing(rel)]
    result["valid"] = not result["required_missing"]
    result["version"] = _read_version(path)
    result["has_venv"] = _has_venv(path)
    return result


def _record(path: Path) -> dict:
    info = inspect_root(str(path))
    return {
        "path": str(path),
        "drive": path.drive or path.anchor or str(path),
        "version": info["version"],
        "has_venv": info["has_venv"],
        "complete": not info["optional_missing"],
        "optional_missing": info["optional_missing"],
    }


# ---------------------------------------------------------------- BFS 扫描
def _scan_drive(root: str, deadline: float) -> bool:
    """BFS 一块盘，命中即记入 _STATE；返回是否被截断。"""
    queue: deque[tuple[Path, int]] = deque([(Path(root), 0)])
    while queue:
        if _CANCEL.is_set() or time.monotonic() > deadline:
            return True
        with _LOCK:
            truncated = _STATE["scanned_dirs"] >= DIR_LIMIT or len(_STATE["found"]) >= FIND_LIMIT
        if truncated:
            return True
        path, depth = queue.popleft()
        with _LOCK:
            _STATE["scanned_dirs"] += 1
            _save_locked()
        version = _identify(path)
        if version is not None:
            rec = _record(path)
            with _LOCK:
                if not any(f["path"].lower() == rec["path"].lower() for f in _STATE["found"]):
                    _STATE["found"].append(rec)
                    _save_locked(force=True)
            continue  # 命中后不再下钻（models/custom_nodes 庞大且无意义）
        if depth >= DEPTH_LIMIT:
            continue
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith((".", "$")) or name.lower() in _DIR_SKIP:
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            queue.append((Path(entry.path), depth + 1))
    return False


def _thread_body() -> None:
    try:
        drives, excluded = _enumerate_drives()
        with _LOCK:
            _STATE.update(drives=drives, excluded=excluded)
            _save_locked(force=True)
        deadline = time.monotonic() + TIME_LIMIT
        truncated = False
        for drive in drives:
            if _CANCEL.is_set():
                truncated = True
                break
            with _LOCK:
                _STATE["current_drive"] = drive
                _save_locked(force=True)
            if _scan_drive(drive, deadline):
                truncated = True
                break
        with _LOCK:
            _STATE["found"].sort(key=lambda f: f["path"].lower())
            _STATE.update(
                status="done", current_drive=None, truncated=truncated,
                cancelled=_CANCEL.is_set(), finished_ms=_now_ms(),
            )
            _save_locked(force=True)
    except Exception as exc:  # noqa: BLE001 —— 后台线程兜底，错误透出到状态接口
        with _LOCK:
            _STATE.update(status="error", error=str(exc)[:300], finished_ms=_now_ms())
            _save_locked(force=True)


def recover_interrupted() -> int:
    """启动恢复：运行态收敛为 interrupted，只读扫描也不自动重放。"""
    global _JOB_ID
    changed = 0
    jobs = sorted(
        _store().list(_NAMESPACE),
        key=lambda job: float(job.get("created_ms") or 0),
    )
    for stored in jobs:
        if stored.get("status") != "running":
            continue
        job = _copy_state(stored)
        job.update(
            status="interrupted",
            current_drive=None,
            error="桥接器重启，本次扫描已中断；请按需重新发起扫描",
            finished_ms=_now_ms(),
        )
        _store().save(_NAMESPACE, str(job["id"]), job)
        changed += 1
    if jobs:
        latest = _store().get(_NAMESPACE, str(jobs[-1].get("id") or ""))
        _JOB_ID = str(latest.get("id")) if latest else None
        if latest:
            with _LOCK:
                _STATE.update(_copy_state(latest))
    _CANCEL.clear()
    return changed


def reset() -> None:
    """测试辅助：清空扫描任务快照与内存状态。"""
    global _JOB_ID, _LAST_PERSIST_DIRS
    with _LOCK:
        _store().clear(_NAMESPACE)
        _JOB_ID = None
        _LAST_PERSIST_DIRS = 0
        _CANCEL.clear()
        _STATE.update({
            "id": None,
            "status": "idle",
            "created_ms": None,
            "drives": [],
            "excluded": [],
            "current_drive": None,
            "scanned_dirs": 0,
            "found": [],
            "truncated": False,
            "cancelled": False,
            "error": None,
            "started_ms": None,
            "finished_ms": None,
        })

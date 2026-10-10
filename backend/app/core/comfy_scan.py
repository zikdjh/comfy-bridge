"""本机 ComfyUI 安装扫描（设置 → 本地「扫描本机」）。

单槽后台线程：枚举固定/可移动磁盘（按要求排除 C 盘），BFS 按目录特征
「main.py + comfy/ + comfy_extras/」识别 ComfyUI 仓库根；命中后不再下钻
（其下 models 等目录庞大且无意义）。设总时限 / 目录数 / 命中数上限防失控，
超限如实标记 truncated。结果存进程内存，后端重启即清空。
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
EXCLUDED_LETTERS = {"c"}     # 用户明确要求：扫描排除 C 盘
DEPTH_LIMIT = 9              # 距盘根的最大下钻层数（实际安装可能嵌在很深的工程目录里）
DIR_LIMIT = 150_000          # 目录枚举总量上限（防极端大盘拖死）
FIND_LIMIT = 30              # 命中数上限
TIME_LIMIT = 180.0           # 扫描总时限（秒）

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
_STATE: dict = {
    "status": "idle",          # idle | running | done | error
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


def state() -> dict:
    with _LOCK:
        return dict(_STATE, found=[dict(f) for f in _STATE["found"]])


def start() -> None:
    """启动一次扫描；已有扫描在跑时抛 RuntimeError（API 层转 409）。"""
    with _LOCK:
        if _STATE["status"] == "running":
            raise RuntimeError("扫描已在进行中")
        _CANCEL.clear()
        _STATE.update(
            status="running", drives=[], excluded=[], current_drive=None,
            scanned_dirs=0, found=[], truncated=False, cancelled=False,
            error=None, started_ms=_now_ms(), finished_ms=None,
        )
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
        version = _identify(path)
        if version is not None:
            rec = _record(path)
            with _LOCK:
                if not any(f["path"].lower() == rec["path"].lower() for f in _STATE["found"]):
                    _STATE["found"].append(rec)
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
        deadline = time.monotonic() + TIME_LIMIT
        truncated = False
        for drive in drives:
            if _CANCEL.is_set():
                truncated = True
                break
            with _LOCK:
                _STATE["current_drive"] = drive
            if _scan_drive(drive, deadline):
                truncated = True
                break
        with _LOCK:
            _STATE["found"].sort(key=lambda f: f["path"].lower())
            _STATE.update(
                status="done", current_drive=None, truncated=truncated,
                cancelled=_CANCEL.is_set(), finished_ms=_now_ms(),
            )
    except Exception as exc:  # noqa: BLE001 —— 后台线程兜底，错误透出到状态接口
        with _LOCK:
            _STATE.update(status="error", error=str(exc)[:300], finished_ms=_now_ms())

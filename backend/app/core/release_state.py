"""整合包版本、首次升级备份与启动自检记录。

本模块不依赖 FastAPI 生命周期，启动器也可直接调用。版本状态文件放在用户
配置目录，与解压目录解耦；覆盖安装程序文件不会丢失版本历史或备份。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import config_dir

STATE_NAME = "release-state.json"
SELF_CHECK_NAME = "startup-self-check.json"
MAX_HISTORY = 20
LEGACY_PREVIOUS = "untracked"

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:[-.]?(?P<pre>beta|rc|a|alpha|b)[-.]?(?P<prenum>\d+))?$",
    re.IGNORECASE,
)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 2, "rc": 3}
_STABLE_RANK = 4


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _version_key(tag: str) -> tuple[int, int, int, int, int] | None:
    match = _VERSION_RE.match(tag.strip())
    if not match:
        return None
    pre = (match.group("pre") or "").lower()
    rank = _STABLE_RANK if not pre else _PRE_RANK.get(pre, -1)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        rank,
        int(match.group("prenum") or 0),
    )


def is_valid_version(tag: str) -> bool:
    return _version_key(tag) is not None


def is_newer(candidate: str, current: str) -> bool:
    left, right = _version_key(candidate), _version_key(current)
    return bool(left and right and left > right)


def _candidate_version_files() -> list[Path]:
    # 源码开发树没有 version.txt；整合包中包根位于
    # runtime/Lib/site-packages/app/core/release_state.py 之上第五层。
    here = Path(__file__).resolve()
    return [
        Path(os.environ.get("CB_BUNDLE_VERSION_FILE", "")) if os.environ.get(
            "CB_BUNDLE_VERSION_FILE", ""
        ).strip() else Path(),
        here.parents[3] / "version.txt",
        here.parents[4] / "version.txt",
        here.parents[5] / "version.txt",
    ]


def read_current_version() -> tuple[str | None, Path | None]:
    for path in _candidate_version_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            continue
        if text:
            return text, path
    return None, None


def state_path() -> Path:
    return config_dir() / STATE_NAME


def self_check_path() -> Path:
    return config_dir() / SELF_CHECK_NAME


def read_state() -> dict[str, Any]:
    return _read_json(state_path())


def has_existing_user_data() -> bool:
    """判断版本状态缺失时是否已有需要保护的旧安装数据。

    旧版本没有 release-state.json；只要配置、请求库、图快照、上传元数据、
    审计日志或既有备份任一存在，就不能把当前启动当作干净首次安装。
    """
    home = config_dir()
    if (home / "config.toml").is_file():
        return True
    if (home / "audit.log").is_file():
        return True
    if any((home / "backups").glob("ComfyBridge-backup-*.zip")):
        return True
    if any((home / "uploads").glob("*/meta.json")):
        return True

    from ..logs import store

    database = Path(store.DB_PATH)
    if database.is_file() and database.stat().st_size > 0:
        return True
    graphs = Path(store.GRAPH_DIR)
    return graphs.is_dir() and any(graphs.iterdir())


def _backup_id(row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    value = row.get("name") or row.get("id")
    return str(value) if value else None


def prepare_upgrade(
    *,
    current_version: str | None = None,
    backup_factory: Callable[..., dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """记录版本并确保新版本首次运行时存在升级备份。

    首次安装没有既有数据，不强制备份；同版本重启是幂等的；任何版本变化都
    必须先成功创建备份，否则抛异常，让启动流程停在数据库迁移之前。
    """
    version, source = read_current_version()
    version = (current_version or version or "").strip() or None
    if not version:
        return {"status": "not_packaged", "version": None, "previous_version": None}

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    state = read_state()
    previous = str(state.get("current_version") or "") or None
    history = list(state.get("history") or [])
    backups = list(state.get("upgrade_backups") or [])

    if previous == version:
        return {
            "status": "current",
            "version": version,
            "previous_version": previous,
            "installed_at_ms": state.get("installed_at_ms"),
            "backup": next(
                (row for row in backups if row.get("version") == version), None
            ),
        }

    # beta7 及更早版本没有版本状态文件。已有用户数据但缺少状态时，必须按
    # 旧安装升级处理，而不是按干净首次安装跳过迁移前备份。
    legacy_adoption = previous is None and has_existing_user_data()
    effective_previous = LEGACY_PREVIOUS if legacy_adoption else previous
    is_upgrade = legacy_adoption or (
        previous is not None and is_newer(version, previous)
    )
    backup: dict[str, Any] | None = None
    if effective_previous is not None:
        if backup_factory is None:
            from .maintenance import create_backup

            backup_factory = create_backup
        backup = backup_factory(
            reason=f"automatic_before_upgrade:{effective_previous}->{version}"
        )
        backups.insert(0, {
            "version": version,
            "previous_version": effective_previous,
            "name": _backup_id(backup),
            "created_ms": now,
        })
        backups = backups[:MAX_HISTORY]

    history.insert(0, {
        "version": version,
        "previous_version": effective_previous,
        "installed_ms": now,
        "upgrade": is_upgrade,
        "legacy_adoption": legacy_adoption,
        "source": str(source) if source else None,
    })
    next_state = {
        "format": 1,
        "current_version": version,
        "previous_version": effective_previous,
        "installed_at_ms": now,
        "history": history[:MAX_HISTORY],
        "upgrade_backups": backups,
    }
    _atomic_json(state_path(), next_state)
    return {
        "status": (
            "legacy_upgraded"
            if legacy_adoption
            else ("upgraded" if is_upgrade else ("version_changed" if previous else "installed"))
        ),
        "version": version,
        "previous_version": effective_previous,
        "installed_at_ms": now,
        "backup": backup,
    }


def record_self_check(
    *,
    ok: bool,
    checks: list[dict[str, Any]],
    error: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    version = version or read_current_version()[0]
    row = {
        "ok": bool(ok),
        "version": version,
        "checked_at_ms": int(time.time() * 1000),
        "checks": checks,
        "error": error,
    }
    _atomic_json(self_check_path(), row)
    return row


def status() -> dict[str, Any]:
    """设置页使用的只读快照；坏文件只影响对应字段，不阻塞页面。"""
    state = read_state()
    check = _read_json(self_check_path())
    version, source = read_current_version()
    return {
        "version": state.get("current_version") or version,
        "previous_version": state.get("previous_version"),
        "installed_at_ms": state.get("installed_at_ms"),
        "version_file": str(source) if source else None,
        "history": state.get("history") or [],
        "upgrade_backups": state.get("upgrade_backups") or [],
        "last_self_check": check or None,
        "packaged": bool(version),
    }

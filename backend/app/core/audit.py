"""管理面审计日志（P2-1）。

只记录请求元数据：时间、方法、路径、状态码、客户端 IP、Origin、认证方式与结果。
**绝不落盘 Authorization / token / 请求体**，避免把新引入的凭证写进日志文件。

落盘位置：``CB_AUDIT_LOG_PATH`` 覆盖，否则 ``<config_dir>/audit.log``（JSONL）。
单文件超过 ``_MAX_BYTES`` 时轮转一份 ``audit.log.1``，只保留一代，防止无限增长。
写入失败只记 logger，绝不因为审计问题阻断请求。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .config import config_dir

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024  # 5MB 轮转一次，够用且不会拖慢管理面


def audit_log_path() -> Path:
    override = os.environ.get("CB_AUDIT_LOG_PATH", "").strip()
    return Path(override) if override else config_dir() / "audit.log"


def audit_enabled() -> bool:
    """显式开关 ``CB_AUDIT_LOG`` 可强制开启/关闭；默认跟随「是否配置了管理令牌」。"""
    flag = os.environ.get("CB_AUDIT_LOG", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    from .management_auth import management_token

    return bool(management_token())


def _rotate(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < _MAX_BYTES:
        return
    backup = path.with_name(path.name + ".1")
    backup.unlink(missing_ok=True)
    path.replace(backup)


def record(event: dict) -> None:
    """追加一条审计记录（JSONL）；任何异常都只记日志，不影响请求。"""
    entry = {"ts_ms": int(time.time() * 1000), **event}
    path = audit_log_path()
    try:
        line = json.dumps(entry, ensure_ascii=False)
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("审计日志写入失败: %s", exc)

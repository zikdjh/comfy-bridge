"""Modal 访问网关：只读卷操作走 Python SDK，其余命令保留 CLI 子进程。

凭证优先级：设置页保存的 token_id/token_secret（SDK / CLI 都注入
MODAL_TOKEN_ID/MODAL_TOKEN_SECRET）> ~/.modal.toml。只填了管理台凭证的用户也能直接用。

SDK 只读卷操作（modal 1.5.x 实测）：
- list_dir / list_files / volumes_list / volume_exists 返回既有 CLI 形状的 dict，
  调用方与前端不需要感知 SDK 改造；同时新增整数 size_bytes / mtime_s，供用量统计
  直接累加。

CLI 契约仍保留的部分：volume put/get/rm/create/delete、app list/logs、billing、
container 等继续走子进程；`volume put/get/rm` 的调用方按批次 2 迁移。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any

from ..core.config import get_settings

_EXE: str | None = None
logger = logging.getLogger(__name__)

# SDK 客户端与 Volume 句柄进程内缓存；账号切换由 reset_runtime_state() 失效。
_CLIENT = None
_CLIENT_KEY: tuple[str, str] | None = None
_CLIENT_LOOP: object | None = None
_VOLUMES: dict[tuple[str, str], Any] = {}

_modal: Any = None
_MODAL_IMPORT_FAILED = False


def _get_modal():
    """首次 SDK 调用才 import modal；避免 pytest/CLI-only 启动路径提前加载 SDK。"""
    global _modal, _MODAL_IMPORT_FAILED
    if _modal is not None:
        return _modal
    if _MODAL_IMPORT_FAILED:
        error = ModalCliError(
            "modal SDK 不可用（请安装：pip install 'modal>=1.5'）",
            category="not_installed",
        )
        _record_failure(error)
        raise error
    try:
        import modal as modal_module
    except ImportError as exc:  # pragma: no cover - 运行环境缺 SDK 时走此分支
        _MODAL_IMPORT_FAILED = True
        error = ModalCliError(
            "modal SDK 不可用（请安装：pip install 'modal>=1.5'）",
            category="not_installed",
        )
        _record_failure(error)
        raise error from exc
    _modal = modal_module
    # 与 CLI 子进程同一网络边界：Modal 云端通信不走本机代理。
    # Modal 1.5+ 的官方开关只影响其自身 gRPC/HTTP 通道，不触碰其他库。
    os.environ.setdefault("MODAL_DISABLE_API_PROXY", "1")
    return _modal

_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?i?B)", re.IGNORECASE)

# CLI quirk：`volume ls --json` 的 filename 对非 ASCII 双重转义（\u98ce 以字面 backslash+u 落地），
# json.loads 一次后仍是 "\\u98ce" 字面串；真实中文与 ASCII 名不受影响，仅名字含该模式时还原。
_DOUBLE_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape_cli_name(text: str) -> str:
    if "\\u" not in text:
        return text
    try:
        out = _DOUBLE_ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), text)
        out.encode("utf-8")  # 代理对拆出的孤立代理项在此失败 → 保留原样
        return out
    except (ValueError, OverflowError, UnicodeEncodeError):
        return text
_SIZE_UNITS = {
    "B": 1,
    "KB": 1024, "KIB": 1024,
    "MB": 1024**2, "MIB": 1024**2,
    "GB": 1024**3, "GIB": 1024**3,
    "TB": 1024**4, "TIB": 1024**4,
}


def parse_size(text: str) -> int:
    """把 CLI 的人类可读大小（"6 B" / "1.3 KiB" / "1.2 GB"）解析为字节数。"""
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).upper()])
    except (ValueError, KeyError):
        return 0


def _humanize_filesize(value: int) -> str:
    """对齐 modal.cli.volume.humanize_filesize：1024 基，B 无小数、其余一位小数。"""
    if value < 0:
        raise ValueError("value should be >= 0")
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} EiB"


def _localized_mtime(ts: int) -> str:
    """对齐 timestamp_to_localized_str(ts, False)；<=0 与 CLI 一致落空串。"""
    if not ts or ts <= 0:
        return ""
    dt = datetime.fromtimestamp(ts).astimezone()
    return f"{dt:%Y-%m-%d %H:%M %Z}"


def _localized_datetime_json(ts: int) -> str:
    """对齐 CLI --json 口径：timestamp_to_localized_str(ts, True)。"""
    if not ts or ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).astimezone().isoformat(sep=" ", timespec="seconds")


def _entry_type(entry: Any) -> str:
    """FileEntryType → CLI 的 file/dir/link/fifo/socket 文案。"""
    value = getattr(entry.type, "value", entry.type)
    return {0: "file", 1: "file", 2: "dir", 3: "link", 4: "fifo", 5: "socket"}.get(value, "file")


def _normalize_entry(entry: Any) -> dict:
    """SDK FileEntry → 既有 list_dir dict；size/mtime 保持 CLI 同形字符串。"""
    raw_path = str(getattr(entry, "path", "") or "")
    rel = raw_path.rstrip("/").rsplit("/", 1)[-1]
    size = int(getattr(entry, "size", 0) or 0)
    mtime = int(getattr(entry, "mtime", 0) or 0)
    return {
        "name": _unescape_cli_name(rel),
        "type": _entry_type(entry),
        "size": _humanize_filesize(size),
        "mtime": _localized_mtime(mtime),
        "size_bytes": size,
        "mtime_s": mtime,
    }


def _exe() -> str:
    global _EXE
    if _EXE is None:
        _EXE = shutil.which("modal") or "modal"
    return _EXE


_ERROR_LABELS = {
    "not_installed": "未安装",
    "not_logged_in": "未登录",
    "network": "网络失败",
    "permission": "权限不足",
    "rate_limited": "平台限流",
    "uncertain": "远端状态不确定",
    "command": "命令失败",
}

# 只有明确列出、确实不改远端状态的子命令才允许网络错误自动重试。
# 例如 image/secret/profile 的写子命令、volume rm/put、app shell 一律不在此列。
_READ_ONLY_SUBCOMMANDS = {
    "app": {"list", "logs", "history"},
    "container": {"list", "logs"},
    "volume": {"ls", "list", "get"},
    "billing": {"summary", "report"},
}

# 最近一次 CLI 失败/成功供全局状态页读取；进程内状态，重启清空。
_LAST_FAILURE: dict | None = None
_LAST_SUCCESS_MS: int | None = None


class ModalCliError(RuntimeError):
    """modal CLI 非零退出 / 未安装，附带可操作分类。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "command",
        retryable: bool = False,
        next_action: str | None = None,
        command: str | None = None,
        exit_code: int | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.next_action = next_action or _DEFAULT_ACTIONS.get(category)
        self.command = command
        self.exit_code = exit_code

    @property
    def label(self) -> str:
        return _ERROR_LABELS.get(self.category, self.category)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "message": str(self),
            "retryable": self.retryable,
            "next_action": self.next_action,
            "command": self.command,
            "exit_code": self.exit_code,
        }


_DEFAULT_ACTIONS = {
    "not_installed": "安装 modal CLI 并确认它在 PATH 中：pip install modal",
    "not_logged_in": "执行 modal token new，或在「设置 → Modal」填写 token_id/token_secret",
    "network": "检查本机网络、代理和 Modal 服务状态后重试",
    "permission": "确认当前 Modal 账户对该资源有权限，或更换凭证",
    "rate_limited": "Modal 控制面限流中，稍等片刻重试即可；启动期首检会自动退避重试",
    "uncertain": "先用 modal app list 核对远端状态，确认前不要重复发起写操作",
    "command": "查看错误详情与命令参数后重试",
}


def _classify_error(message: str, *, timed_out: bool = False) -> tuple[str, bool]:
    """CLI 文本 → 稳定错误分类；宁可归为 command，也不误判权限/登录。"""
    low = message.lower()
    if timed_out:
        return "uncertain", False
    if any(marker in low for marker in (
        "token validation failed",
        "invalid token",
        "not authenticated",
        "not logged in",
        "no credentials",
        "credentials not found",
        "authentication failed",
        "unauthorized",
        "401",
    )):
        return "not_logged_in", False
    if any(marker in low for marker in (
        "forbidden",
        "permission denied",
        "access denied",
        "not authorized",
        "403",
    )):
        return "permission", False
    if any(marker in low for marker in (
        "rate limit",
        "too many requests",
        "quota exceeded",
        "429",
    )):
        # Modal 控制面限流（VolumeListFiles 等）：窗口为秒级，只读命令按多级退避自动恢复
        return "rate_limited", True
    if any(marker in low for marker in (
        "connection refused",
        "connection reset",
        "connection timed out",
        "timed out",
        "temporary failure in name resolution",
        "name or service not known",
        "network is unreachable",
        "nodename nor servname",
        "ssl",
        "proxy",
        "server disconnected",
        "bad gateway",
        "service unavailable",
        "connection error",
        "read timeout",
        "connect timeout",
    )):
        return "network", True
    return "command", False


def _record_failure(error: ModalCliError) -> None:
    global _LAST_FAILURE
    _LAST_FAILURE = {
        **error.to_dict(),
        "ts_ms": int(time.time() * 1000),
    }


def _record_success() -> None:
    global _LAST_SUCCESS_MS
    _LAST_SUCCESS_MS = int(time.time() * 1000)


def diagnostics() -> dict:
    """最近一次 CLI 调用状态（供 /health/modal-cli；不发起子进程）。"""
    return {
        "last_failure": dict(_LAST_FAILURE) if _LAST_FAILURE else None,
        "last_success_ms": _LAST_SUCCESS_MS,
    }


def _clear_diagnostics() -> None:
    """测试辅助：清空进程内 CLI 诊断状态。"""
    global _LAST_FAILURE, _LAST_SUCCESS_MS
    _LAST_FAILURE = None
    _LAST_SUCCESS_MS = None


def reset_runtime_state() -> None:
    """账号切换后清空诊断并丢弃缓存的 SDK client / Volume 句柄（config API 联动点）。"""
    global _CLIENT, _CLIENT_KEY, _CLIENT_LOOP
    _clear_diagnostics()
    _CLIENT = None
    _CLIENT_KEY = None
    _CLIENT_LOOP = None
    _VOLUMES.clear()


def sdk_available() -> bool:
    """SDK 是否可导入；批次 3 起前端 cli_found 字段沿用此语义。"""
    if _modal is not None:
        return True
    try:
        _get_modal()
    except ModalCliError:
        return False
    return True


def cli_path() -> str:
    """返回解析到的 modal CLI 路径；未安装时返回空串。"""
    return shutil.which("modal") or ""


def auth_env() -> dict[str, str]:
    """设置页保存的 Modal 凭证 → CLI 环境变量；未配置时返回空（回落 ~/.modal.toml）。

    strip 兜底：历史存量里可能已有带尾部空白的凭证（粘贴带入），
    直接注入会让 CLI 报 Token validation failed。
    """
    modal = get_settings().modal
    token_id, token_secret = modal.token_id.strip(), modal.token_secret.strip()
    if token_id and token_secret:
        return {"MODAL_TOKEN_ID": token_id, "MODAL_TOKEN_SECRET": token_secret}
    return {}


def cli_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """modal CLI 子进程统一环境：凭证 + 静音告警 + 强制 UTF-8 输出。

    Windows 中文控制台下 CLI 的 stdout 默认走 GBK：成功路径会打印 "✓ / 🔨 / 🎉"，
    编码失败直接让进程以非零码退出——实测 `modal deploy` 因此被误判为「部署失败」
    并把版本 pin 回滚（镜像其实已经构建完成）。强制 UTF-8 同时与调用方的
    decode("utf-8") 对称，顺带修掉中文错误消息的乱码。
    """
    env = {
        **os.environ,
        "PYTHONWARNINGS": "ignore",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        **auth_env(),
        **(extra or {}),
    }
    # Modal 云端通信不走本机代理：CLI 的 aiohttp 通道拾取 HTTP(S)_PROXY 后
    # 需要 aiohttp-socks 额外包，且代理链路不稳定会拖垮所有 CLI 调用。
    # Modal API 端点（api.modal.com 等）由 Modal 自己的边缘网络保证可达性。
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    return env


def proxy_credentials() -> tuple[str, str] | None:
    """Runner 端点代理鉴权凭证对（proxy_token_id, proxy_token_secret）。

    代理鉴权 token 是独立凭证族（wk-…/ws-…，仪表盘创建），与 ~/.modal.toml 里的
    账户 API token（ak-/as-）不通——CLI 那条 token 文件兜底路径在这里不成立，
    未配置就返回 None，由调用方决定报错形态。仅在内存使用，不落日志不回显。
    """
    modal = get_settings().modal
    token_id, token_secret = modal.proxy_token_id.strip(), modal.proxy_token_secret.strip()
    if token_id and token_secret:
        return token_id, token_secret
    return None


def volume_name() -> str:
    return get_settings().modal.volume


def _popen(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    try:
        # PYTHONWARNINGS=ignore：modal CLI 启动时向 stderr 打 DeprecationWarning，
        # 污染所有错误消息（破坏 "No such file or directory" 等匹配）
        return subprocess.Popen(
            [_exe(), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=cli_env(extra_env),
        )
    except FileNotFoundError as exc:
        error = ModalCliError(
            "找不到 modal CLI（请安装：pip install modal，并确认在 PATH 中）",
            category="not_installed",
            command="modal " + " ".join(args),
        )
        _record_failure(error)
        raise error from exc


def _is_read_only(args: tuple[str, ...]) -> bool:
    """仅查询类子命令可安全重试；写命令由调用方决定是否重发。"""
    if len(args) < 2:
        return False
    return args[1] in _READ_ONLY_SUBCOMMANDS.get(args[0], set())


def _should_retry(error: ModalCliError, args: tuple[str, ...]) -> bool:
    return error.retryable and _is_read_only(args)


# 类型化 SDK 操作使用与 CLI 命令串等价的只读白名单；写操作不自动重发。
_READ_ONLY_OPS = {
    "list_dir",
    "list_files",
    "volumes_list",
    "volume_exists",
    "app_status",
    "billing_summary",
    "billing_report",
}


def _should_retry_op(error: ModalCliError, op: str) -> bool:
    return error.retryable and op in _READ_ONLY_OPS


async def _with_retry(op: str, fn):
    attempt = 0
    while True:
        try:
            return await fn()
        except ModalCliError as exc:
            if not _should_retry_op(exc, op):
                raise
            delays = _RETRY_DELAYS.get(exc.category, _DEFAULT_RETRY_DELAYS)
            if attempt >= len(delays):
                raise
            delay = delays[attempt]
            attempt += 1
            logger.info(
                "modal SDK 可重试错误（%s/%s），%.1fs 后第 %d 次重试: %s",
                op, exc.category, delay, attempt, exc,
            )
            await asyncio.sleep(delay)


# 各错误类别的重试退避序列（秒）：限流窗口为秒级，退避逐次拉长；
# 其余可重试错误（网络抖动等）保持原有的单次快速重试。
_RETRY_DELAYS: dict[str, tuple[float, ...]] = {
    "rate_limited": (1.0, 4.0, 10.0),
}
_DEFAULT_RETRY_DELAYS: tuple[float, ...] = (0.5,)


async def run(*args: str, timeout: float = 600.0) -> str:
    """执行 CLI 命令并返回 stdout；阻塞部分放到线程池，避免占用事件循环。

    timeout 兜底防止 CLI 网络僵死把整条决策链路（/health、/prompt 路由）挂死。
    只读命令按类别退避重试：限流 1s/4s/10s 三次，网络抖动 0.5s 一次；写命令不重发。
    """
    attempt = 0
    while True:
        try:
            return await _run_once(*args, timeout=timeout)
        except ModalCliError as exc:
            if not _should_retry(exc, args):
                raise
            delays = _RETRY_DELAYS.get(exc.category, _DEFAULT_RETRY_DELAYS)
            if attempt >= len(delays):
                raise
            delay = delays[attempt]
            attempt += 1
            logger.info(
                "modal CLI 可重试错误（%s），%.1fs 后第 %d 次重试: %s",
                exc.category, delay, attempt, exc,
            )
            await asyncio.sleep(delay)


async def _run_once(*args: str, timeout: float) -> str:
    proc = _popen(*args)
    try:
        out, err = await asyncio.wait_for(asyncio.to_thread(proc.communicate), timeout=timeout)
    except TimeoutError:
        proc.kill()
        error = ModalCliError(
            f"modal {args[0]} 超时（超过 {timeout:g}s），已终止",
            category="uncertain",
            command="modal " + " ".join(args),
        )
        _record_failure(error)
        raise error from None
    if proc.returncode != 0:
        msg = (err or out).decode("utf-8", errors="replace").strip()
        msg = "\n".join(  # 兜底：剥掉 CLI 的 DeprecationWarning 噪音行
            line for line in msg.splitlines()
            if "DeprecationWarning" not in line and "set_event_loop_policy" not in line
        ).strip()
        raw = msg[:400] or f"modal {args[0]} 退出码 {proc.returncode}"
        category, retryable = _classify_error(raw)
        error = ModalCliError(
            raw,
            category=category,
            retryable=retryable,
            command="modal " + " ".join(args),
            exit_code=proc.returncode,
        )
        _record_failure(error)
        raise error
    _record_success()
    return out.decode("utf-8", errors="replace")


def cli_json_list(text: str) -> list:
    """从 CLI stdout 提取 JSON 数组：输出可能混有 banner/日志行，定位首个 '[' 截取；无则空列表。

    JSON 本身不合法时照常抛 JSONDecodeError，由调用方的既有异常路径处理。
    """
    start = text.find("[")
    return json.loads(text[start:]) if start != -1 else []


async def _get_client():
    """按凭证对缓存进程内 SDK client；配置了管理台 token 时优先 from_credentials。"""
    global _CLIENT, _CLIENT_KEY, _CLIENT_LOOP
    modal = _get_modal()
    loop = asyncio.get_running_loop()
    if _CLIENT is not None and _CLIENT_LOOP is not loop:
        _CLIENT = None
        _CLIENT_KEY = None
        _VOLUMES.clear()
    modal_cfg = get_settings().modal
    token_id = modal_cfg.token_id.strip()
    token_secret = modal_cfg.token_secret.strip()
    key = (token_id, token_secret)
    if _CLIENT is not None and _CLIENT_KEY == key:
        return _CLIENT
    try:
        if token_id and token_secret:
            _CLIENT = await modal.Client.from_credentials.aio(token_id, token_secret)
        else:
            _CLIENT = await modal.Client.from_env.aio()
    except Exception as exc:
        _CLIENT = None
        error = _map_sdk_error(exc, "获取 Modal 客户端")
        _record_failure(error)
        raise error from exc
    _CLIENT_KEY = key
    _CLIENT_LOOP = loop
    return _CLIENT


async def _get_volume(
    name: str | None = None, *, use_cache: bool = True, record_failure: bool = True
):
    """按 (client, volume_name) 缓存已 hydrate 的 Volume 句柄。"""
    modal = _get_modal()
    vol_name = name or volume_name()
    client = await _get_client()
    key = (str(id(client)), vol_name)
    cached = _VOLUMES.get(key) if use_cache else None
    if cached is not None:
        return cached
    try:
        handle = modal.Volume.from_name(vol_name, client=client)
        await handle.hydrate.aio(client)
    except Exception as exc:
        error = _map_sdk_error(exc, f"获取数据卷 {vol_name}")
        if record_failure:
            _record_failure(error)
        raise error from exc
    _VOLUMES[key] = handle
    return handle


_SDK_ERROR_MAP = {
    "AuthError": "not_logged_in",
    "PermissionDeniedError": "permission",
    "ResourceExhaustedError": "rate_limited",
    "ConnectionError": "network",
    "TimeoutError": "network",
    "VolumeUploadTimeoutError": "network",
    "NotFoundError": "uncertain",
    "AlreadyExistsError": "command",
    "ConflictError": "command",
    "InvalidError": "command",
    "VersionError": "command",
    "ClientClosed": "command",
}


def _map_sdk_error(exc: Exception, context: str) -> ModalCliError:
    """modal SDK 异常 → 既有 ModalCliError 分类契约；未知异常保守归 command。"""
    name = type(exc).__name__
    category = _SDK_ERROR_MAP.get(name, "command")
    retryable = category in ("network", "rate_limited")
    msg = str(exc).strip() or name
    if name == "NotFoundError":
        # 保留既有调用方的字符串兼容判断（空目录/不存在路径按空处理）。
        msg = f"No such file or directory: {msg}"
    return ModalCliError(
        f"{context}失败：{msg}"[:400],
        category=category,
        retryable=retryable,
    )


async def list_dir(remote_path: str, volume: str | None = None, *, timeout: float = 600.0) -> list[dict]:
    """列一层目录；SDK FileEntry 归一化为既有 CLI dict 形状。"""
    async def _call() -> list[dict]:
        try:
            handle = await _get_volume(volume)
            entries = await asyncio.wait_for(handle.listdir.aio(remote_path or "/"), timeout=timeout)
        except ModalCliError:
            raise
        except TimeoutError as exc:
            error = ModalCliError(f"列出数据卷目录超时（超过 {timeout:g}s）", category="uncertain")
            _record_failure(error)
            raise error from exc
        except Exception as exc:
            error = _map_sdk_error(exc, "列出数据卷目录")
            _record_failure(error)
            raise error from exc
        out = [_normalize_entry(entry) for entry in entries]
        _record_success()
        return out

    return await _with_retry("list_dir", _call)


async def list_files(remote_dir: str, max_depth: int = 3, *, timeout: float = 600.0) -> list[dict]:
    """递归收集目录下所有文件，name 为相对 remote_dir 的 POSIX 路径。"""
    files: list[dict] = []
    deadline = asyncio.get_running_loop().time() + timeout

    async def walk(rel_dir: str, depth: int) -> None:
        path = f"{remote_dir}/{rel_dir}" if rel_dir else remote_dir
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        for entry in await list_dir(path, timeout=remaining):
            rel = f"{rel_dir}/{entry['name']}" if rel_dir else entry["name"]
            if entry["type"] in ("dir", "directory"):
                if depth < max_depth:
                    await walk(rel, depth + 1)
            else:
                files.append({**entry, "name": rel})

    await walk("", 0)
    return files


async def volumes_list(*, timeout: float = 600.0) -> list[dict]:
    """所有数据卷 [{name, created_at, created_by}]，与 CLI volume list --json 同形。"""
    async def _call() -> list[dict]:
        try:
            client = await _get_client()
            modal = _get_modal()
            handles = await asyncio.wait_for(
                modal.Volume.objects.list.aio(client=client), timeout=timeout
            )
            out: list[dict] = []
            for handle in handles:
                info = await handle.info.aio()
                created = info.created_at
                out.append({
                    "name": info.name or "",
                    "created_at": _localized_datetime_json(int(created.timestamp())),
                    "created_by": info.created_by or "",
                })
        except ModalCliError:
            raise
        except TimeoutError as exc:
            error = ModalCliError(f"列出数据卷超时（超过 {timeout:g}s）", category="uncertain")
            _record_failure(error)
            raise error from exc
        except Exception as exc:
            error = _map_sdk_error(exc, "列出数据卷")
            _record_failure(error)
            raise error from exc
        _record_success()
        return out

    return await _with_retry("volumes_list", _call)


async def volume_exists(name: str, *, timeout: float = 600.0) -> bool:
    """单对象 hydrate 判断卷是否存在；NotFoundError → False，其余异常按契约抛出。"""
    async def _call() -> bool:
        try:
            await asyncio.wait_for(
                _get_volume(name, use_cache=False, record_failure=False), timeout=timeout
            )
        except ModalCliError as exc:
            if exc.category == "uncertain":
                return False
            raise
        except TimeoutError:
            return False
        except Exception as exc:
            error = _map_sdk_error(exc, f"检查数据卷 {name}")
            _record_failure(error)
            raise error from exc
        _record_success()
        return True

    return await _with_retry("volume_exists", _call)

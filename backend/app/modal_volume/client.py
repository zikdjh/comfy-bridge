"""modal CLI 子进程封装（桥接器不直连 Modal API）。

凭证优先级：设置页保存的 token_id/token_secret（注入 MODAL_TOKEN_ID/MODAL_TOKEN_SECRET
环境变量）> CLI 自读的 ~/.modal.toml。只填了管理台凭证的用户也能直接用。

CLI 输出契约（modal 1.5.x 实测）：
- volume ls <vol> <path> --json
    → [{"filename": 含查询前缀的全路径, "type": "file"|"dir",
        "size": 人类可读串（如 "6 B" / "1.2 GB"）, "created_modified": 本地时间串}]
    不支持递归，深目录需逐层展开。
- volume put <vol> <local> [remote]   单文件/单目录上传；自动创建父目录；-f 覆盖
- volume get <vol> <remote> [dest]    下载；remote 为目录时递归
- volume rm <vol> <remote> [-r]       删除
- volume list --json                  所有卷 [{name, created_at, created_by}]（无大小）
- volume create <name>                新建卷
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

from ..core.config import get_settings

_EXE: str | None = None
logger = logging.getLogger(__name__)

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


async def run(*args: str, timeout: float = 600.0) -> str:
    """执行 CLI 命令并返回 stdout；阻塞部分放到线程池，避免占用事件循环。

    timeout 兜底防止 CLI 网络僵死把整条决策链路（/health、/prompt 路由）挂死。
    """
    try:
        return await _run_once(*args, timeout=timeout)
    except ModalCliError as exc:
        if not _should_retry(exc, args):
            raise
        logger.info("modal CLI 可重试错误，500ms 后重试一次: %s", exc)
        await asyncio.sleep(0.5)
        return await _run_once(*args, timeout=timeout)


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


async def list_dir(remote_path: str, volume: str | None = None) -> list[dict]:
    """列一层目录；filename 归一化为相对 remote_path 的名称。

    CLI quirk：`volume ls <vol> /` 会报 No such file or directory，省略路径参数才是列根。
    """
    args = ["volume", "ls", volume or volume_name()]
    prefix = ""
    if remote_path and remote_path not in ("/", "."):
        args.append(remote_path)
        prefix = remote_path.rstrip("/") + "/"
    out = await run(*args, "--json")
    items = cli_json_list(out)
    entries: list[dict] = []
    for item in items:
        full = str(item.get("filename", ""))
        rel = full[len(prefix):].strip("/") if prefix and full.startswith(prefix) else full.rsplit("/", 1)[-1]
        rel = rel.strip("/")
        if not rel:
            continue
        entries.append({
            "name": _unescape_cli_name(rel),
            "type": item.get("type", "file"),
            "size": str(item.get("size", "")),
            "mtime": str(item.get("created_modified", "")),
        })
    return entries


async def list_files(remote_dir: str, max_depth: int = 3) -> list[dict]:
    """递归收集目录下所有文件，name 为相对 remote_dir 的 POSIX 路径。"""
    files: list[dict] = []

    async def walk(rel_dir: str, depth: int) -> None:
        path = f"{remote_dir}/{rel_dir}" if rel_dir else remote_dir
        for entry in await list_dir(path):
            rel = f"{rel_dir}/{entry['name']}" if rel_dir else entry["name"]
            if entry["type"] in ("dir", "directory"):
                if depth < max_depth:
                    await walk(rel, depth + 1)
            else:
                files.append({**entry, "name": rel})

    await walk("", 0)
    return files

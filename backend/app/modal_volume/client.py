"""Modal 访问网关：控制面数据操作走 Python SDK，保留 CLI 的部分继续用子进程。

凭证优先级：设置页保存的 token_id/token_secret（SDK / CLI 都注入
MODAL_TOKEN_ID/MODAL_TOKEN_SECRET）> ~/.modal.toml。只填了管理台凭证的用户也能直接用。

SDK 操作（modal 1.5.x 实测）：
- list_dir / list_files / volumes_list / volume_exists 返回既有 CLI 形状的 dict，
  调用方与前端不需要感知 SDK 改造；同时新增整数 size_bytes / mtime_s，供用量统计
  直接累加。
- put_file / put_many / put_directory / get_file / remove_file / volume_create /
  volume_delete 是类型化写操作入口。上传使用 batch_upload 上下文，错误集中在退出时；
  下载自己补齐父目录与临时文件原子替换语义。
- app_status / billing_summary / billing_report 复用同一 SDK client 与凭证缓存；
  应用状态字符串保持 Modal CLI 的既有展示口径，调用方与前端契约不变。

CLI 契约仍保留的部分：deploy、modal run 流式作业、container 管理等继续走子进程。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.config import get_settings
from ..core.constants import GPU_TYPE

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
    """首次 SDK 调用才 import modal；避免 pytest/无 SDK 启动路径提前加载 SDK。"""
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
        "name": rel,
        "type": _entry_type(entry),
        "size": _humanize_filesize(size),
        "mtime": _localized_mtime(mtime),
        "size_bytes": size,
        "mtime_s": mtime,
    }


def _exe() -> str:
    global _EXE
    if _EXE is None:
        # 解析顺序：解释器同目录的 modal 入口 → PATH 上的 modal → 回落
        # `python -m modal`（当前解释器自身）。
        #
        # 第三级是整合包场景的关键：pip 生成的 modal.exe 启动器把构建机的
        # 绝对解释器路径写进 shebang，随包分发后必然失效，因此整合包 runtime
        # 不带 Scripts/ 入口；此时只要 SDK 已装在本解释器里，`python -m modal`
        # 与 console script 等价，CLI 通道（deploy / container）无需外部安装。
        venv_exe = Path(sys.executable).with_name("modal.exe")
        if not venv_exe.is_file():  # POSIX：无 .exe 后缀
            venv_exe = Path(sys.executable).with_name("modal")
        if venv_exe.is_file():
            _EXE = str(venv_exe)
        else:
            _EXE = shutil.which("modal") or str(sys.executable)
    return _EXE


def cli_exe() -> str:
    """modal CLI 可执行路径（deploy 等子进程通道共用，避免各处重复解析漂移）。"""
    return _exe()


def cli_argv(*args: str) -> list[str]:
    """完整 CLI argv：console script 直接接参数，回落解释器时补 `-m modal`。"""
    exe = _exe()
    if Path(exe).name.lower().startswith("python"):
        return [exe, "-m", "modal", *args]
    return [exe, *args]


_ERROR_LABELS = {
    "not_installed": "未安装",
    "not_logged_in": "未登录",
    "network": "网络失败",
    "permission": "权限不足",
    "rate_limited": "平台限流",
    "uncertain": "远端状态不确定",
    "command": "命令失败",
}

# 最近一次 Modal 调用失败/成功供全局状态页读取；进程内状态，重启清空。
_LAST_FAILURE: dict | None = None
_LAST_SUCCESS_MS: int | None = None


class ModalCliError(RuntimeError):
    """Modal SDK / CLI 调用失败，附带可操作分类。"""

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
    "not_installed": "安装包含 Python SDK 的 modal 依赖：pip install 'modal>=1.5'",
    "not_logged_in": "执行 modal token new，或在「设置 → Modal」填写 token_id/token_secret",
    "network": "检查本机网络、代理和 Modal 服务状态后重试",
    "permission": "确认当前 Modal 账户对该资源有权限，或更换凭证",
    "rate_limited": "Modal 控制面限流中，稍等片刻重试即可；启动期首检会自动退避重试",
    "uncertain": "先在 Modal 控制台核对远端状态，确认前不要重复发起写操作",
    "command": "查看错误详情与命令参数后重试",
}


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
    """最近一次 Modal 调用状态（供 /health/modal-cli；不发起 IO）。"""
    return {
        "last_failure": dict(_LAST_FAILURE) if _LAST_FAILURE else None,
        "last_success_ms": _LAST_SUCCESS_MS,
    }


def _clear_diagnostics() -> None:
    """测试辅助：清空进程内 Modal 诊断状态。"""
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


def _forget_volume(name: str) -> None:
    for key in [key for key in _VOLUMES if key[1] == name]:
        _VOLUMES.pop(key, None)


def sdk_available() -> bool:
    """SDK 是否可导入；批次 3 起前端 cli_found 字段沿用此语义。"""
    if _modal is not None:
        return True
    try:
        _get_modal()
    except ModalCliError:
        return False
    return True


def sdk_version() -> str:
    """返回 Modal Python SDK 版本；不可导入时抛既有类型化错误。"""
    modal = _get_modal()
    version = str(getattr(modal, "__version__", "") or "").strip()
    if not version:
        error = ModalCliError(
            "modal SDK 缺少版本信息（请重新安装：pip install 'modal>=1.5'）",
            category="not_installed",
        )
        _record_failure(error)
        raise error
    return version


def _assert_app_proto_contract() -> None:
    """启动自检：AppList proto 字段仍在；升级 modal 后尽早暴露非公开 API 变更。"""
    try:
        from modal_proto import api_pb2

        fields = api_pb2.AppListResponse.DESCRIPTOR.fields_by_name
        apps_field = fields.get("apps")
        app_fields = apps_field.message_type.fields_by_name if apps_field is not None else {}
        if "apps" not in fields or "description" not in app_fields or "state" not in app_fields:
            raise AttributeError("AppListResponse.apps / AppStats.description,state 缺失")
    except Exception as exc:
        error = ModalCliError(
            f"modal AppList proto 契约不兼容: {exc}",
            category="command",
        )
        _record_failure(error)
        raise error from exc


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
    # Modal 云端通信默认不走本机代理：CLI 的 aiohttp 通道拾取 HTTP(S)_PROXY 后
    # 需要 aiohttp-socks 额外包，且代理链路不稳定会拖垮所有 CLI 调用。
    # Modal API 端点（api.modal.com 等）由 Modal 自己的边缘网络保证可达性。
    # bridge.use_system_proxy=True 时保留系统代理变量，交还给 CLI 自行处理。
    if not get_settings().bridge.use_system_proxy:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(key, None)
        env["NO_PROXY"] = "*"
    else:
        env.pop("NO_PROXY", None)
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
            cli_argv(*args),
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
    "rate_limited": (1.0, 2.0),
}
_DEFAULT_RETRY_DELAYS: tuple[float, ...] = (0.5,)


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


def _normalize_remote(local: Any, remote: str) -> str:
    """对齐 CLI volume put：远端以 / 结尾时，把本地文件名追加到目录后。"""
    value = str(remote or "").replace("\\", "/")
    if not value:
        raise ModalCliError("远端路径不能为空", category="command")
    if value.endswith("/"):
        if isinstance(local, (str, Path)):
            name = Path(local).name
        else:
            name = str(getattr(local, "name", "") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name:
            raise ModalCliError("远端路径以 / 结尾时无法确定文件名", category="command")
        value += name
    return value


async def _run_sdk_write(op: str, context: str, timeout: float, fn):
    """执行写操作：不自动重试；超时归 uncertain（远端可能已部分生效）。"""
    try:
        return await asyncio.wait_for(fn(), timeout=timeout)
    except ModalCliError:
        raise
    except TimeoutError as exc:
        error = ModalCliError(
            f"{context}超时（超过 {timeout:g}s）",
            category="uncertain",
        )
        _record_failure(error)
        raise error from exc
    except Exception as exc:
        error = _map_sdk_error(exc, context)
        _record_failure(error)
        raise error from exc


_SDK_ERROR_MAP = {
    "AuthError": "not_logged_in",
    "PermissionDeniedError": "permission",
    "ResourceExhaustedError": "rate_limited",
    "ConnectionError": "network",
    "TimeoutError": "network",
    "VolumeUploadTimeoutError": "network",
    "ServiceError": "network",
    "InternalError": "network",
    "DataLossError": "network",
    "UnimplementedError": "command",
    "RemoteError": "command",
    "WorkspaceManagementError": "command",
    "NotFoundError": "uncertain",
    "FileNotFoundError": "uncertain",
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
    if name in ("NotFoundError", "FileNotFoundError"):
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
            # The SDK reports a missing prefix (including a removed final child)
            # as NotFoundError/FileNotFoundError; CLI list treated both as an empty
            # directory. Keep that contract for all existing callers.
            if type(exc).__name__ in ("NotFoundError", "FileNotFoundError"):
                _record_success()
                return []
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


async def put_file(
    local_path: Path | str | Any,
    remote: str,
    volume: str | None = None,
    *,
    force: bool = True,
    timeout: float = 600.0,
) -> None:
    """上传一个文件；兼容 CLI 的远端目录尾缀语义，错误在 batch 退出时映射。"""
    remote_path = _normalize_remote(local_path, remote)

    async def _do() -> None:
        handle = await _get_volume(volume)
        async with handle.batch_upload.aio(force=force) as batch:
            batch.put_file(local_path, remote_path)

    await _run_sdk_write("put_file", f"上传文件到 {remote_path}", timeout, _do)
    _record_success()


async def put_directory(
    local_dir: Path | str,
    remote: str,
    volume: str | None = None,
    *,
    force: bool = True,
    recursive: bool = True,
    timeout: float = 600.0,
) -> None:
    """递归上传本地目录；CLI 的远端尾缀目录语义由 _normalize_remote 对齐。"""
    if not Path(local_dir).is_dir():
        raise ModalCliError(f"本地目录不存在: {local_dir}", category="command")
    remote_path = _normalize_remote(local_dir, remote)

    async def _do() -> None:
        handle = await _get_volume(volume)
        async with handle.batch_upload.aio(force=force) as batch:
            batch.put_directory(local_dir, remote_path, recursive=recursive)

    await _run_sdk_write("put_directory", f"上传目录到 {remote_path}", timeout, _do)
    _record_success()


async def put_many(
    items: list[tuple[Path | str | Any, str]],
    volume: str | None = None,
    *,
    force: bool = True,
    timeout: float = 600.0,
) -> None:
    """把多个本地文件/目录放进同一个 batch，合并为一次提交。"""
    if not items:
        return

    async def _do() -> None:
        handle = await _get_volume(volume)
        async with handle.batch_upload.aio(force=force) as batch:
            for local_path, remote in items:
                remote_path = _normalize_remote(local_path, remote)
                if isinstance(local_path, (str, Path)) and Path(local_path).is_dir():
                    batch.put_directory(local_path, remote_path)
                else:
                    batch.put_file(local_path, remote_path)

    await _run_sdk_write("put_many", f"批量上传 {len(items)} 项", timeout, _do)
    _record_success()


async def get_file(
    remote: str,
    local_destination: Path | str,
    volume: str | None = None,
    *,
    force: bool = True,
    timeout: float = 600.0,
) -> None:
    """下载远端文件；目录递归展开，写入临时文件后原子替换目标。"""
    remote_path = str(remote or "").replace("\\", "/")
    if not remote_path:
        raise ModalCliError("远端路径不能为空", category="command")
    destination = Path(local_destination)

    async def _do() -> None:
        handle = await _get_volume(volume)
        entries = await handle.listdir.aio(remote_path, recursive=False)
        if not entries:
            raise FileNotFoundError(remote_path)
        first_path = str(getattr(entries[0], "path", "")).rstrip("/")
        # SDK 的 FileEntry.path 不带前导 "/"，而调用方通常传 CLI 形态的绝对卷路径。
        if len(entries) == 1 and first_path.lstrip("/") == remote_path.rstrip("/").lstrip("/"):
            entry = entries[0]
            if _entry_type(entry) == "dir":
                await _download_directory(handle, remote_path, destination, force)
            else:
                target = destination / Path(remote_path).name if destination.is_dir() else destination
                await _download_one(handle, first_path, target, force)
            return
        await _download_directory(handle, remote_path, destination, force)

    await _run_sdk_write("get_file", f"下载 {remote_path}", timeout, _do)
    _record_success()


async def _download_one(handle: Any, remote_path: str, destination: Path, force: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        raise FileExistsError(f"本地目标已存在: {destination}")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fp:
            await handle.read_file_into_fileobj.aio(remote_path, fp)
        os.replace(tmp_path, destination)
    finally:
        tmp_path.unlink(missing_ok=True)


async def _download_directory(
    handle: Any,
    remote_path: str,
    destination: Path,
    force: bool,
) -> None:
    entries = await handle.listdir.aio(remote_path, recursive=True)
    base = remote_path.rstrip("/").lstrip("/")
    for entry in entries:
        if _entry_type(entry) != "file":
            continue
        rel = str(getattr(entry, "path", "") or "").lstrip("/")
        if base:
            prefix = base + "/"
            rel = rel.removeprefix(prefix)
        target = destination / PurePosixPath(rel)
        await _download_one(handle, str(getattr(entry, "path", "")), target, force)


async def remove_file(
    remote: str,
    volume: str | None = None,
    *,
    recursive: bool = False,
    timeout: float = 600.0,
) -> None:
    """删除卷内文件或目录；不存在时保留 NotFoundError 映射。"""
    remote_path = str(remote or "").replace("\\", "/")

    async def _do() -> None:
        handle = await _get_volume(volume)
        await handle.remove_file.aio(remote_path, recursive=recursive)

    await _run_sdk_write("remove_file", f"删除 {remote_path}", timeout, _do)
    _record_success()


async def volume_create(name: str, *, timeout: float = 600.0) -> None:
    """创建整卷；已存在错误按 SDK AlreadyExistsError 映射为 command。"""
    async def _do() -> None:
        client = await _get_client()
        modal = _get_modal()
        await modal.Volume.objects.create.aio(name, client=client)

    await _run_sdk_write("volume_create", f"创建数据卷 {name}", timeout, _do)
    _forget_volume(name)
    _record_success()


async def volume_delete(name: str, *, timeout: float = 600.0) -> None:
    """删除整卷并清理本地 Volume 句柄缓存。"""
    async def _do() -> None:
        client = await _get_client()
        modal = _get_modal()
        await modal.Volume.objects.delete.aio(name, client=client)

    await _run_sdk_write("volume_delete", f"删除数据卷 {name}", timeout, _do)
    _forget_volume(name)
    _record_success()


# Modal CLI app list 的展示口径；app_state 是前端硬契约，映射必须逐字一致。
_APP_STATE_NAMES = {
    0: "unknown",
    1: "ephemeral",
    2: "ephemeral (detached)",
    3: "deployed",
    4: "stopping...",
    5: "stopped",
    6: "initializing...",
    7: "disabled",
    8: "ephemeral (detached)",
    9: "unknown",
}


def _app_state_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    try:
        return _APP_STATE_NAMES.get(int(raw), "unknown")
    except (TypeError, ValueError):
        return "unknown"


_APP_LIST_BLOCKING: Any = None


async def _app_list_on_sdk_loop(client: Any, request: Any, *, timeout: float) -> Any:
    """AppList RPC 本体：必须运行在 modal SDK 的 synchronizer 循环上。"""
    return await client.stub.AppList(request, retry=None, timeout=timeout)


def _app_list_blocking():
    """把 AppList 调用包成同步入口，经 synchronicity 送进 SDK 自己的事件循环。

    modal.Client.from_credentials.aio 把 client 打开在 SDK synchronizer 的后台
    循环上，账单 / 卷等 .aio 操作也都提交到该循环执行。若在调用方循环（uvicorn）
    上裸调 client.stub.AppList，会与 client 的取消上下文 / gRPC 连接跨事件循环
    共享：SDK 打 "RPC request ... made outside of task context" 告警，且首个
    RPC 所建连接被另一循环复用时偶发 "attached to a different loop" 类失败
    （FIXLIST 三十八 P2）。modal CLI 的 `modal app list` 同样经 create_blocking
    通道执行 AppList，这里与其保持一致。
    """
    global _APP_LIST_BLOCKING
    if _APP_LIST_BLOCKING is None:
        from modal._utils.async_utils import synchronizer

        _APP_LIST_BLOCKING = synchronizer.create_blocking(_app_list_on_sdk_loop)
    return _APP_LIST_BLOCKING


async def app_status(app_name: str, *, timeout: float = 20.0) -> str | None:
    """按应用名返回 Modal CLI 同口径状态；不存在返回 None。

    Modal SDK 没有公开的 App.objects.list() API，这里沿用 CLI 同源的内部 stub
    （AppListRequest/AppListResponse），升级 modal 时由状态 golden 用例暴露 proto
    变更。RPC 经 _app_list_blocking 在 SDK synchronizer 循环上执行（见其注释）。
    """
    _assert_app_proto_contract()

    async def _call() -> str | None:
        try:
            from modal_proto import api_pb2

            client = await _get_client()
            env_name = get_settings().modal.environment.strip()
            request = api_pb2.AppListRequest(environment_name=env_name)
            # RPC 自带 deadline（retry=None 时才允许设 timeout）；外层 wait_for
            # 只兜底线程 join（SDK 循环卡死时的上限），悬挂请求不再占线程。
            response = await asyncio.wait_for(
                asyncio.to_thread(_app_list_blocking(), client, request, timeout=timeout),
                timeout=timeout + 10.0,
            )
        except ModalCliError:
            raise
        except TimeoutError as exc:
            error = ModalCliError(f"查询应用状态超时（超过 {timeout:g}s）", category="uncertain")
            _record_failure(error)
            raise error from exc
        except Exception as exc:
            error = _map_sdk_error(exc, "查询应用状态")
            _record_failure(error)
            raise error from exc
        state = next(
            (_app_state_name(item.state) for item in response.apps if item.description == app_name),
            None,
        )
        _record_success()
        return state

    return await _with_retry("app_status", _call)


def _decimal_value(value: Any) -> Decimal | None:
    """Decimal / 字符串 / 数值 → Decimal；无法换算返回 None。"""
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _decimal_float(value: Any) -> float | None:
    """Decimal / 字符串 / 数值 → 4 位小数 float；无法换算返回 None。"""
    number = _decimal_value(value)
    return round(float(number), 4) if number is not None else None


async def billing_summary(cycle: str, *, timeout: float = 45.0) -> dict:
    """单月账单摘要（Workspace.billing.summary），返回 modal_billing 的现有形状。"""
    async def _call() -> dict:
        try:
            modal = _get_modal()
            client = await _get_client()
            workspace = modal.Workspace.from_context(client=client)
            summary = await asyncio.wait_for(
                workspace.billing.summary.aio(cycle=cycle),
                timeout=timeout,
            )
        except ModalCliError:
            raise
        except TimeoutError as exc:
            error = ModalCliError(f"查询账单摘要超时（超过 {timeout:g}s）", category="uncertain")
            _record_failure(error)
            raise error from exc
        except Exception as exc:
            error = _map_sdk_error(exc, "查询账单摘要")
            _record_failure(error)
            raise error from exc

        adjustments = summary.adjustments or {}
        breakdown: dict[str, float] = {}
        for key, value in (summary.metered_cost_breakdown or {}).items():
            number = _decimal_float(value)
            if number:
                breakdown[key] = number
        data = {
            "ok": True,
            "metered_cost_usd": _decimal_float(summary.metered_cost),
            "billed_cost_usd": _decimal_float(summary.billed_cost),
            "credits_applied_usd": abs(_decimal_float(adjustments.get("credits")) or 0.0),
            "plan_cost_usd": _decimal_float(adjustments.get("plan_cost")) or 0.0,
            "breakdown": dict(sorted(breakdown.items(), key=lambda kv: -kv[1])),
        }
        _record_success()
        return data

    return await _with_retry("billing_summary", _call)


async def billing_report(start: datetime, end: datetime, *, timeout: float = 45.0) -> dict:
    """账单明细聚合（Workspace.billing.report），返回现有 resources/apps/daily 形状。"""
    async def _call() -> dict:
        try:
            modal = _get_modal()
            client = await _get_client()
            workspace = modal.Workspace.from_context(client=client)
            rows = await asyncio.wait_for(
                workspace.billing.report.aio(start=start, end=end, resolution="d"),
                timeout=timeout,
            )
        except ModalCliError:
            raise
        except TimeoutError as exc:
            error = ModalCliError(f"查询账单明细超时（超过 {timeout:g}s）", category="uncertain")
            _record_failure(error)
            raise error from exc
        except Exception as exc:
            error = _map_sdk_error(exc, "查询账单明细")
            _record_failure(error)
            raise error from exc

        daily: dict[str, dict[str, Decimal]] = {}
        resources: dict[str, Decimal] = {}
        apps: dict[str, Decimal] = {}
        for row in rows:
            interval = getattr(row, "interval_start", None)
            date = interval.strftime("%Y-%m-%d") if isinstance(interval, datetime) else ""
            cost_by_resource = getattr(row, "cost_by_resource", None) or {}
            for resource, value in cost_by_resource.items():
                usd = _decimal_value(value) or Decimal(0)
                if date:
                    bucket = daily.setdefault(
                        date, {"gpu_usd": Decimal(0), "total_usd": Decimal(0)}
                    )
                    bucket["total_usd"] += usd
                    if resource == GPU_TYPE:
                        bucket["gpu_usd"] += usd
                resources[resource] = resources.get(resource, Decimal(0)) + usd
            app = str(getattr(row, "description", "") or getattr(row, "object_id", "") or "未知应用")
            row_total = sum(
                (_decimal_value(value) or Decimal(0)) for value in cost_by_resource.values()
            )
            apps[app] = apps.get(app, Decimal(0)) + row_total
        data = {
            "ok": True,
            "gpu_usd": round(float(resources.get(GPU_TYPE, Decimal(0))), 4),
            "total_usd": round(float(sum(resources.values(), Decimal(0))), 4),
            "resources": {
                k: round(float(v), 4)
                for k, v in sorted(resources.items(), key=lambda kv: -kv[1])
            },
            "apps": [
                {"name": k, "usd": round(float(v), 4)}
                for k, v in sorted(apps.items(), key=lambda kv: -kv[1])
            ],
            "daily": {
                d: {
                    "gpu_usd": round(float(v["gpu_usd"]), 4),
                    "total_usd": round(float(v["total_usd"]), 4),
                }
                for d, v in sorted(daily.items())
            },
        }
        _record_success()
        return data

    return await _with_retry("billing_report", _call)

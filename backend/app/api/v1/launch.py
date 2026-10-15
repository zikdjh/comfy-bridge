"""启动命令 API（管理台「设置 → 启动命令」）。

GET  /api/v1/launch  两端启动命令视图：
  - local：实时探测 :8188 监听进程的完整命令行 + 进程控制状态（60s 缓存）；
  - modal：runner.py 的基础参数（镜像内固定）+ 已保存的追加参数。

PUT  /api/v1/launch 保存两端追加启动参数到 ~/.comfybridge/config.toml，并把
云端参数经 SDK 网关下发到数据卷 /config/launch_args.json
（runner.py 容器冷启动时读取追加；正在运行的容器不受影响）。

进程控制（桥接器直接控制 ComfyUI 启动，见 core/local_proc.py）：
  GET  /launch/local/status    本地实例状态（运行中/归属/启动日志尾部）
  POST /launch/local/start     启动本地实例（外部占用需 takeover 确认）
  POST /launch/local/stop      停止本地实例（外部托管需 force 确认）
  POST /launch/local/restart   重启本地实例（使追加参数生效）
  GET  /launch/modal/containers  云端运行中容器清单（45s 进程内缓存，M4-2 面板数据源）
  POST /launch/modal/stop      停止云端运行中的容器（下次冷启动读取新参数）
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core import local_proc
from ...core.config import CONFIG_PATH, AppConfig, get_settings
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError, cli_env, cli_exe
from ...runner import client as runner_client
from .config import _dump_toml

router = APIRouter(prefix="/launch", tags=["launch"])

# runner.py launch_comfyui() 的固定基础参数（镜像内写死，改动需重新 modal deploy）
MODAL_BASE_ARGS = [
    "--listen", "0.0.0.0",
    "--port", "8188",
    "--disable-auto-launch",
    "--base-directory", "/data",
]
# 数据卷上的启动参数文件（PUT 时经 SDK 网关写入）
VOLUME_ARGS_FILE = "/config/launch_args.json"
# 云端是固定端口/固定基准目录的 web_server，这几个参数由 runner.py 掌控，不允许覆盖
MODAL_PROTECTED = {"--listen", "--port", "--base-directory"}
# 值 token 不允许出现的字符（防注入/防拆行；Windows 路径的反斜杠、冒号不受影响）
_BAD_CHARS = re.compile(r"""[\s"'`&|;<>()$\r\n]""")
# 基础命令 argv 专用：空格合法（下发时经 list2cmdline 加引号），其余 cmd 元字符仍禁止
_ARGV_BAD_CHARS = re.compile(r"""["`&|;<>()$\r\n%]""")


# ---------------------------------------------------------------- 视图
def _local_launch_view() -> dict:
    settings = get_settings()
    det = local_proc.detect_local_command(local_proc.LOCAL_PORT)
    state = local_proc.read_state()
    return {
        "detected": det is not None,
        "managed_by": state.get("managed_by") if state.get("running") else None,
        "command": (det or {}).get("command")
            or " ".join(settings.local.launch_command)
            or f'python ComfyUI\\main.py --listen 127.0.0.1 --port {local_proc.LOCAL_PORT}',
        "pid": state.get("pid"),
        "extra_args": settings.local.extra_launch_args,
        "fallback": det is None,
        "control": {
            "running": state.get("running", False),
            "owned": state.get("owned", False),
            "starting": state.get("starting", False),
            "pid": state.get("pid"),
            "managed_by": state.get("managed_by"),
            "uptime_ms": state.get("uptime_ms"),
        },
    }


def _modal_launch_view() -> dict:
    settings = get_settings()
    return {
        "base_args": MODAL_BASE_ARGS,
        "extra_args": settings.modal.extra_launch_args,
        "volume": settings.modal.volume,
        "volume_file": VOLUME_ARGS_FILE,
        "app_state": runner_client.deployed_state(),
    }


@router.get("")
async def read_launch() -> dict:
    return {"local": _local_launch_view(), "modal": _modal_launch_view()}


# ---------------------------------------------------------------- 保存与下发
class LaunchBody(BaseModel):
    local_extra: list[str] = []
    modal_extra: list[str] = []
    # 基础命令整体覆盖（argv）；不传 = 首次保存时从当前实例探测固化
    local_command: list[str] | None = None


def _validate(args: list[str], target: str) -> None:
    for token in args:
        if not token or _BAD_CHARS.search(token):
            raise HTTPException(status_code=422, detail=f"{target} 参数不合法: {token!r}")
        if token.startswith("--"):
            base = token.split("=", 1)[0]
            if target == "modal" and base in MODAL_PROTECTED:
                raise HTTPException(status_code=422, detail=f"云端参数 {base} 由 runner.py 固定，不可覆盖")


async def _push_modal_args(args: list[str]) -> dict:
    """经 SDK 网关把参数文件写入数据卷（供 runner.py 冷启动读取）。"""
    settings = get_settings()
    payload = json.dumps(args, ensure_ascii=False).encode("utf-8")
    try:
        await volume_cli.put_file(
            io.BytesIO(payload),
            VOLUME_ARGS_FILE,
            volume=settings.modal.volume,
            force=True,
            timeout=180,
        )
    except ModalCliError as exc:
        return {"ok": False, "detail": str(exc) or type(exc).__name__}
    return {"ok": True, "detail": f"{settings.modal.volume}:{VOLUME_ARGS_FILE}"}


@router.put("")
async def write_launch(body: LaunchBody) -> dict:
    local_extra = [t.strip() for t in body.local_extra if t.strip()]
    modal_extra = [t.strip() for t in body.modal_extra if t.strip()]
    _validate(local_extra, "local")
    _validate(modal_extra, "modal")

    current = get_settings()
    if body.local_command is not None:
        for token in body.local_command:
            if not token or _ARGV_BAD_CHARS.search(token):
                raise HTTPException(status_code=422, detail=f"基础命令参数不合法: {token!r}")
        launch_command = [t.strip() for t in body.local_command if t.strip()]
    elif current.local.launch_command:
        launch_command = current.local.launch_command
    else:
        # 首次保存：把当前实例的启动命令固化为桥接器接管启动的基础命令
        det = local_proc.detect_local_command(local_proc.LOCAL_PORT)
        launch_command = (
            local_proc.canonical_from_detection(det, Path(get_settings().local.comfy_root))
            if det else []
        )
    clean = AppConfig.model_validate({
        "local": {
            **current.local.model_dump(),
            "extra_launch_args": local_extra,
            "launch_command": launch_command,
        },
        "bridge": current.bridge.model_dump(),
        "modal": {**current.modal.model_dump(), "extra_launch_args": modal_extra},
    })
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        _dump_toml({
            "local": clean.local.model_dump(),
            "bridge": clean.bridge.model_dump(),
            "modal": clean.modal.model_dump(),
        }),
        encoding="utf-8",
    )
    get_settings.cache_clear()

    push = await _push_modal_args(modal_extra)
    return {"saved": True, "local": _local_launch_view(), "modal": _modal_launch_view(), "volume_push": push}


# ---------------------------------------------------------------- 本地进程控制
class ProcBody(BaseModel):
    takeover: bool = False   # start/restart：端口被外部占用时结束对方并接管
    force: bool = False      # stop：外部托管实例也强制结束
    timeout: float = 150.0   # start/restart：等待就绪秒数


@router.get("/local/status")
async def local_proc_status(tail: bool = False) -> dict:
    return await asyncio.to_thread(local_proc.read_state, tail)


@router.post("/local/start")
async def local_start(body: ProcBody) -> dict:
    return await asyncio.to_thread(local_proc.start, body.takeover, min(body.timeout, 300))


@router.post("/local/stop")
async def local_stop(body: ProcBody) -> dict:
    return await asyncio.to_thread(local_proc.stop, body.force)


@router.post("/local/restart")
async def local_restart(body: ProcBody) -> dict:
    return await asyncio.to_thread(local_proc.restart, body.takeover, min(body.timeout, 300))


# ---------------------------------------------------------------- 云端容器控制
def _container_rows() -> tuple[list[dict], int, str | None]:
    """`modal container list --json` → (本应用容器行, 缺 app_name 的行数, 错误信息)。

    归属只认 app_name 与当前配置相等的行：字段缺失/改名的行宁可不碰，
    避免契约变化时误操作账号下所有应用的容器。
    """
    settings = get_settings()
    app_name = settings.modal.app_name
    try:
        proc = subprocess.run(
            [cli_exe(), "container", "list", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
            env=cli_env(),
        )
    except Exception as exc:  # noqa: BLE001
        return [], 0, str(exc) or type(exc).__name__
    if proc.returncode != 0:
        return [], 0, (proc.stderr or proc.stdout or "").strip()[-300:]
    rows: list = []
    try:
        data = json.loads(proc.stdout or "[]")
        rows = data if isinstance(data, list) else []
    except json.JSONDecodeError:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    own, unknown = [], 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "app_name" not in row:
            unknown += 1
            continue
        if row.get("app_name") != app_name:
            continue
        own.append(row)
    return own, unknown, None


_CONTAINER_CACHE_TTL = 45.0
_containers_cache: tuple[float, dict] | None = None


def _list_modal_containers() -> dict:
    """本应用运行中容器清单（CLI 调用约 1~3s，进程内 45s 缓存；停止容器后失效）。"""
    global _containers_cache
    now = time.monotonic()
    if _containers_cache is not None and now - _containers_cache[0] < _CONTAINER_CACHE_TTL:
        return _containers_cache[1]
    own, unknown, error = _container_rows()
    if error:
        data: dict[str, Any] = {"ok": False, "count": None, "containers": [], "detail": error}
    else:
        containers = []
        for row in own:
            cid = row.get("container_id") or row.get("id")
            if not cid:
                continue
            item: dict = {"id": cid}
            for key in ("started_at", "cpu", "memory", "gpu", "region"):
                if key in row:
                    item[key] = row[key]
            containers.append(item)
        if containers:
            detail = f"{len(containers)} 个运行中容器"
        elif unknown:
            detail = f"container list 返回的 {unknown} 行缺少 app_name 字段，无法判定归属"
        else:
            detail = "当前没有运行中的云端容器"
        data = {
            "ok": True,
            "count": len(containers),
            "containers": containers,
            "unknown_rows": unknown,
            "detail": detail,
        }
    _containers_cache = (now, data)
    return data


def _stop_modal_containers() -> dict:
    """结束当前应用的运行中容器：新参数下次冷启动即生效（相当于立即缩容）。"""
    global _containers_cache
    own, unknown, error = _container_rows()
    if error:
        return {"ok": False, "detail": error}
    ids = [cid for row in own if (cid := row.get("container_id") or row.get("id"))]

    stopped, failed = [], []
    for cid in ids:
        try:
            s = subprocess.run(
                [cli_exe(), "container", "stop", cid, "-y"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
                env=cli_env(),
            )
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{cid}: {exc}")
            continue
        if s.returncode == 0:
            stopped.append(cid)
        else:
            failed.append(f"{cid}: {(s.stderr or s.stdout or '').strip()[-120:]}")
    if ids:
        detail = f"已停止 {len(stopped)}/{len(ids)} 个容器"
    elif unknown:
        detail = f"container list 返回的 {unknown} 行缺少 app_name 字段，无法判定归属，未停止任何容器"
    else:
        detail = "当前没有运行中的云端容器"
    result = {
        "ok": not failed,
        "running": len(ids),
        "stopped": stopped,
        "failed": failed,
        "detail": detail,
    }
    _containers_cache = None  # 停止后立刻失效，面板下次读取即反映新状态
    return result


@router.get("/modal/containers")
async def modal_containers_list() -> dict:
    return await asyncio.to_thread(_list_modal_containers)


@router.post("/modal/stop")
async def modal_containers_stop() -> dict:
    return await asyncio.to_thread(_stop_modal_containers)

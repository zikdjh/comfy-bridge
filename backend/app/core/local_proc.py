"""本地 ComfyUI 进程接管：桥接器直接启动/停止/重启本地实例。

与 ComfyUI Desktop 托管的关系：
- 桥接启动的实例记入 ~/.comfybridge/local_process.json（pid / argv / started_ms），
  重启桥接器后凭该记录继续识别归属；
- 外部（Desktop 等）托管的实例桥接默认不结束，强制接管需显式确认
  （启动流程含端口抢占检测，Desktop 若自动重启会被识别并如实报告）。

uv venv 细节：`.venv/Scripts/python.exe` 是启动器（shim），真正的解释器是它
的子进程——因此桥接经 `cmd /c start /b` 间接拉起：一是让 ComfyUI 脱离桥接
器进程树（后端被强杀/重启不影响已启动的实例），二是无法直接拿到真实服务
pid，就绪归属用「拉起前端口为空 + 首个占用进程的命令行包含我们的启动命令
标记」判定后写归属记录。

启动日志落 ~/.comfybridge/local_comfyui.log，状态接口可回传尾部排错。
"""

from __future__ import annotations

import functools
import json
import logging
import subprocess
import threading
import time
from pathlib import Path

import httpx

from .config import config_dir, get_settings

logger = logging.getLogger(__name__)

LOCAL_PORT = 8188
_STATE_FILE = config_dir() / "local_process.json"
_LOG_FILE = config_dir() / "local_comfyui.log"
# start/stop/restart 全程串行（API 层经 to_thread 并发调用；restart 内部再调 stop/start，
# 用可重入锁避免自锁）
_PROC_LOCK = threading.RLock()


def _locked(fn):
    """串行化进程控制：并发 start/stop 会双双通过运行态检查重复拉起/互踩。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _PROC_LOCK:
            return fn(*args, **kwargs)

    return wrapper

# ---------------------------------------------------------------- 进程/端口探测

# 探测缓存（命令行读取要走 PowerShell，单次 1~2s）
_DETECT_CACHE: dict = {"at": 0.0, "data": None}
_DETECT_TTL = 60.0


def pid_listening(port: int) -> int | None:
    """netstat 找出监听端口的 PID（仅 Windows 开发机；失败返回 None）。"""
    try:
        # 中文 Windows 上 netstat 输出是 GBK：errors="replace" 保证解码不失败
        # （需要解析的 LISTENING 行本身是纯 ASCII）
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        ).stdout or ""
    except Exception:  # noqa: BLE001 —— 探测失败是正常路径（非 Windows 等）
        return None
    for line in out.splitlines():
        parts = line.split()
        # TCP  127.0.0.1:8188  0.0.0.0:0  LISTENING  <pid>
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def detect_local_command(port: int) -> dict | None:
    """读取监听进程的完整命令行（PowerShell Get-CimInstance，Windows only）。"""
    now = time.monotonic()
    cached = _DETECT_CACHE["data"]
    if now - _DETECT_CACHE["at"] < _DETECT_TTL:
        if cached is None:
            return None  # 负结果同样缓存，避免每次状态读取都跑一遍 netstat
        if cached.get("pid") == pid_listening(port):
            return cached
    pid = pid_listening(port)
    if pid is None:
        _DETECT_CACHE.update(at=now, data=None)
        return None
    try:
        ps = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    if not out:
        return None
    # 命令行拆解：exe 可能带引号（路径含空格）也可能不带
    if out.startswith('"'):
        exe = out[1:].split('"', 1)[0]
        rest = out[1:].split('"', 1)[1].strip()
    else:
        exe, _, rest = out.partition(" ")
    det = {"pid": pid, "command": out, "exe": exe, "args": rest.split()}
    _DETECT_CACHE.update(at=now, data=det)
    return det


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        ).stdout or ""
    except Exception:  # noqa: BLE001
        return False
    return f'"{pid}"' in out


def _kill(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # 不向上抛：调用方随后的端口释放检查会如实报告"仍被占用"
        logger.warning("taskkill 结束 PID %s 失败: %s", pid, exc)


def _parent_info(pid: int) -> tuple[int | None, str | None]:
    """返回 (父进程 PID, 进程 exe 名)；查询失败返回 (None, None)。"""
    try:
        ps = (
            f'$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}";'
            f'if($p){{"$($p.ParentProcessId);$($p.Name)"}}'
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        ).stdout.strip()
        pp, name = out.split(";", 1)
        return int(pp), name
    except Exception:  # noqa: BLE001
        return None, None


def _is_our_spawn(port_pid: int) -> bool:
    """监听进程是否由桥接器（cmd start /b 间接）拉起。

    桥接拉起的父链：python shim（uv venv 启动器，存活）→ cmd（已退出）；
    ComfyUI Desktop 自动重启的父进程是存活的非 python 进程（Desktop 本体）。
    判定不了时按乐观归属处理（拉起前端口已确保为空）。
    """
    ppid, pname = _parent_info(port_pid)
    if ppid is None:
        return True
    if not _pid_alive(ppid):
        return True
    if pname and pname.lower().startswith("python"):
        return True
    return False


# ---------------------------------------------------------------- 归属记录

def _read_state_file() -> dict | None:
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 文件缺失/损坏视为无记录
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    # pid 类型/值非法（文件被写坏）同样视为无记录，避免 read_state 的 int() 炸 500
    return data if isinstance(pid, int) and pid > 0 else None


def _write_state_file(pid: int, argv: list[str]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(
            {"pid": pid, "argv": argv, "started_ms": int(time.time() * 1000)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _clear_state_file() -> None:
    try:
        _STATE_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _tail_log(lines: int = 25) -> list[str]:
    try:
        text = _LOG_FILE.read_text(encoding="utf-8", errors="replace")
        return text.splitlines()[-lines:]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 启动命令解析

def canonical_from_detection(det: dict, root: Path) -> list[str]:
    """探测命令行 → 规范 argv：exe + 参数，main.py 相对路径改写为绝对路径。"""
    exe = det.get("exe") or ""
    args = list(det.get("args") or [])
    if not exe:
        return []
    argv = [exe, *args]
    for i, tok in enumerate(argv[1:], 1):
        if tok.replace('"', "").lower().endswith("main.py") and not Path(tok).is_absolute():
            argv[i] = str(root / "main.py")
            break
    return argv


def _fallback_argv(root: Path) -> list[str]:
    py = root / ".venv" / "Scripts" / "python.exe"
    return [str(py) if py.is_file() else "python", "-s", str(root / "main.py")]


def canonical_base_argv() -> list[str]:
    """启动基础命令：配置存储 > 实时探测（改写脚本路径）> venv 回退。"""
    settings = get_settings()
    root = Path(settings.local.comfy_root)
    stored = settings.local.launch_command
    if stored and stored[0]:
        return list(stored)
    det = detect_local_command(LOCAL_PORT)
    if det:
        argv = canonical_from_detection(det, root)
        if argv:
            return argv
    return _fallback_argv(root)


# ---------------------------------------------------------------- 状态/控制

def read_state(tail: bool = False) -> dict:
    """本地 ComfyUI 运行状态：桥接管管 / 外部托管 / 启动中 / 未运行。"""
    settings = get_settings()
    port_pid = pid_listening(LOCAL_PORT)
    stored = _read_state_file()
    state: dict = {
        "port": LOCAL_PORT,
        "comfy_url": settings.local.comfy_url,
        "pid": port_pid,
        "running": False,
        "owned": False,
        "starting": False,
    }
    if tail:
        state["log_tail"] = _tail_log()

    if port_pid is not None:
        if stored and port_pid == int(stored.get("pid") or 0):
            state.update(
                running=True, owned=True,
                started_ms=stored.get("started_ms"),
                uptime_ms=int(time.time() * 1000) - int(stored.get("started_ms") or 0),
                command=" ".join(stored.get("argv") or []),
            )
        else:
            # 端口被无归属记录的进程占用
            if stored:
                _clear_state_file()
            if _is_our_spawn(port_pid):
                # 桥接拉起但归属记录丢失（如桥接器在归属写入前重启）
                state.update(running=True, owned=False,
                             managed_by="桥接实例（归属记录丢失）", command=None)
            else:
                det = detect_local_command(LOCAL_PORT)
                state.update(running=True, owned=False,
                             managed_by="ComfyUI Desktop",
                             command=(det or {}).get("command") or None)
    elif stored and _pid_alive(int(stored["pid"])):
        # 桥接子进程还在启动中（尚未监听端口）
        state.update(
            starting=True, owned=True, pid=int(stored["pid"]),
            started_ms=stored.get("started_ms"),
            command=" ".join(stored.get("argv") or []),
        )
    elif stored:
        _clear_state_file()
    return state


def _wait_ready(argv: list[str], timeout: float, t0: float) -> dict:
    """轮询等待新实例就绪并确定归属。

    拉起前端口已确保为空：首个监听进程经父链核验（_is_our_spawn）确认是
    我们经 cmd start 拉起的实例后写归属记录；Desktop 的自动重启会被识别
    并如实报告抢占。
    """
    comfy_url = get_settings().local.comfy_url.rstrip("/")
    attributed = False
    while time.monotonic() - t0 < timeout:
        port_pid = pid_listening(LOCAL_PORT)
        if port_pid is not None:
            if not attributed:
                if not _is_our_spawn(port_pid):
                    _clear_state_file()
                    det = detect_local_command(LOCAL_PORT)
                    return {
                        "ok": False, "pid": port_pid, "conflict": True, "port_pid": port_pid,
                        "error": f"端口 {LOCAL_PORT} 被 ComfyUI Desktop 自动重启抢占（PID {port_pid}，"
                                 f"{(det or {}).get('command') or '命令行未知'}）；请先退出 Desktop 再由桥接器启动。",
                        "log_tail": _tail_log(15),
                        "seconds": round(time.monotonic() - t0, 1),
                    }
                _write_state_file(port_pid, argv)
                attributed = True
            try:
                if httpx.get(f"{comfy_url}/system_stats", timeout=3.0).status_code == 200:
                    return {"ok": True, "pid": port_pid, "ready": True,
                            "seconds": round(time.monotonic() - t0, 1)}
            except Exception:  # noqa: BLE001 —— 端口已监听但应用未就绪，继续等
                pass
        time.sleep(1.5)
    return {
        "ok": False, "pid": pid_listening(LOCAL_PORT),
        "ready": False, "starting": True,
        "error": f"{int(timeout)} 秒内未就绪；进程仍在启动，可稍后刷新状态或查看启动日志",
        "log_tail": _tail_log(15),
        "seconds": round(time.monotonic() - t0, 1),
    }


def _wait_port_free(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pid_listening(LOCAL_PORT) is None:
            return True
        time.sleep(0.5)
    return pid_listening(LOCAL_PORT) is None


@_locked
def start(takeover: bool = False, timeout: float = 150.0) -> dict:
    """以「基础命令 + 已保存追加参数」启动本地 ComfyUI（同步，含就绪等待）。

    经 `cmd /c start /b` 间接拉起：ComfyUI 不挂在桥接器进程树下，后端
    重启/强杀不影响已启动的实例。
    """
    st = read_state()
    if st.get("running") and st.get("owned"):
        return {"ok": False, "pid": st.get("pid"), "error": f"桥接启动的实例已在运行（PID {st['pid']}）"}
    if st.get("running"):
        if not takeover:
            return {
                "ok": False, "conflict": True, "pid": st.get("pid"),
                "error": f"端口 {LOCAL_PORT} 已被外部进程占用（PID {st['pid']}，{st.get('managed_by') or '外部启动'}）"
                         f"。确认「接管启动」将结束该进程并由桥接器重新启动本地 ComfyUI。",
            }
        _kill(int(st["pid"]))
        if not _wait_port_free(15):
            return {"ok": False, "pid": st.get("pid"), "error": "结束外部进程失败：端口仍被占用"}

    argv = canonical_base_argv() + list(get_settings().local.extra_launch_args)
    root = Path(get_settings().local.comfy_root)
    t0 = time.monotonic()
    # start /b：子进程继承重定向的日志句柄；cmd 立即退出，ComfyUI 脱离桥接进程树
    line = f'start "" /b {subprocess.list2cmdline(argv)}'
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("ab") as log:
            subprocess.Popen(
                line, shell=True, cwd=str(root),
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"启动进程失败: {exc}", "command": " ".join(argv)}
    return {**_wait_ready(argv, timeout, t0), "command": " ".join(argv)}


@_locked
def stop(force: bool = False) -> dict:
    """停止本地 ComfyUI：桥接启动的直接停；外部托管需 force 确认。"""
    st = read_state()
    pid = st.get("pid")
    if not st.get("running") and not st.get("starting"):
        return {"ok": True, "stopped": False, "detail": "本地 ComfyUI 未在运行"}
    if st.get("running") and not st.get("owned"):
        if not force:
            return {
                "ok": False, "external": True, "pid": pid,
                "error": f"该实例由 {st.get('managed_by') or '外部进程'} 托管（PID {pid}），桥接默认不结束它；"
                         f"如需停止/接管请确认强制结束。",
            }
    if pid is None:
        return {"ok": False, "error": "未找到目标进程"}
    _kill(int(pid))
    free = _wait_port_free(15)
    _clear_state_file()
    if not free:
        return {"ok": False, "pid": pid, "error": "已发送结束命令，但端口仍被占用"}
    return {"ok": True, "stopped": True, "pid": pid}


@_locked
def restart(takeover: bool = False, timeout: float = 150.0) -> dict:
    """重启本地 ComfyUI（使追加参数生效）。外部托管实例需 takeover 确认。"""
    st = read_state()
    if st.get("running") or st.get("starting"):
        r = stop(force=st.get("owned") or st.get("starting") or takeover)
        if not r.get("ok"):
            return {"ok": False, "stage": "stop", **r}
    return {"stage": "start", **start(takeover=False, timeout=timeout)}

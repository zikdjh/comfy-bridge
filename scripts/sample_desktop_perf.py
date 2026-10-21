#!/usr/bin/env python3
"""采样 ComfyBridge 桌面整合包的启动和空闲资源，输出可检查 JSON。

仅依赖 Windows 自带的 PowerShell/CIM；不访问 Modal、不启动 ComfyUI，也不触发生图。
默认使用空的隔离配置目录，避免读取真实 Modal 凭证或用户请求历史。

示例（在仓库根目录）：
  python scripts/sample_desktop_perf.py --exe tmp/ComfyBridge-0.1.0/comfy-bridge.exe
  python scripts/sample_desktop_perf.py --exe <exe> --cycles 3 --duration 30 --interval 5
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PS = shutil.which("powershell.exe") or "powershell.exe"
STARTUP_TIMEOUT_S = 60.0
CLOSE_TIMEOUT_S = 15.0
PS_PROCESS_SCRIPT = """
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process |
  Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,PrivatePageCount,UserModeTime,KernelModeTime,HandleCount |
  ConvertTo-Json -Compress
"""
@dataclass
class ProcessInfo:
    pid: int
    parent_pid: int
    name: str
    working_set: int
    private_bytes: int
    cpu_seconds: float
    handle_count: int | None


def _ps_processes_once() -> list[ProcessInfo]:
    data = _ps_json(PS_PROCESS_SCRIPT)
    return _rows_to_processes(data)


def _ps_json(script: str) -> Any:
    result = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"PowerShell 采样失败: {detail[:1000]}")
    text = result.stdout.strip()
    if not text:
        return []
    return json.loads(text)


def _rows_to_processes(data: Any) -> list[ProcessInfo]:
    rows = data if isinstance(data, list) else [data]
    out: list[ProcessInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cpu_100ns = int(row.get("UserModeTime") or 0) + int(row.get("KernelModeTime") or 0)
        out.append(
            ProcessInfo(
                pid=int(row.get("ProcessId") or 0),
                parent_pid=int(row.get("ParentProcessId") or 0),
                name=str(row.get("Name") or ""),
                working_set=int(row.get("WorkingSetSize") or 0),
                private_bytes=int(row.get("PrivatePageCount") or 0),
                cpu_seconds=cpu_100ns / 10_000_000,
                handle_count=(int(row["HandleCount"]) if row.get("HandleCount") is not None else None),
            )
        )
    return out


def _tree(root_pid: int, all_processes: list[ProcessInfo]) -> list[ProcessInfo]:
    by_parent: dict[int, list[ProcessInfo]] = {}
    for proc in all_processes:
        by_parent.setdefault(proc.parent_pid, []).append(proc)
    found: list[ProcessInfo] = []
    queue = [root_pid]
    seen: set[int] = set()
    while queue:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        own = next((p for p in all_processes if p.pid == pid), None)
        if own is not None:
            found.append(own)
        queue.extend(child.pid for child in by_parent.get(pid, []))
    return found


def _parse_listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host:
        raise argparse.ArgumentTypeError("监听地址格式应为 HOST:PORT")
    try:
        number = int(port)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1-65535 之间")
    return host, number


def _http_ready(host: str, port: int, timeout_s: float = 1.5) -> bool:
    url = f"http://{host}:{port}/api/v1/health"
    try:
        # 明确绕过宿主机代理，避免测试采样被本地代理端点污染。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout_s) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_ready(host: str, port: int, deadline: float) -> float | None:
    while time.monotonic() < deadline:
        if _http_ready(host, port):
            return time.monotonic()
        time.sleep(0.1)
    return None


def _sample_tree(
    procs: list[ProcessInfo],
    started_at: float,
    sample_no: int,
    elapsed_s: float,
) -> dict[str, Any]:
    return {
        "sample": sample_no,
        "elapsed_s": round(elapsed_s, 3),
        "timestamp": datetime.now(UTC).isoformat(),
        "clock_s": round(time.monotonic() - started_at, 3),
        "processes": [
            {
                "pid": proc.pid,
                "parent_pid": proc.parent_pid,
                "name": proc.name,
                "working_set_mb": round(proc.working_set / 1024 / 1024, 1),
                "private_mb": round(proc.private_bytes / 1024 / 1024, 1),
                "cpu_seconds": round(proc.cpu_seconds, 3),
                "handle_count": proc.handle_count,
            }
            for proc in procs
        ],
        "tree_working_set_mb": round(sum(p.working_set for p in procs) / 1024 / 1024, 1),
        "tree_private_mb": round(sum(p.private_bytes for p in procs) / 1024 / 1024, 1),
        "tree_handle_count": sum(p.handle_count or 0 for p in procs),
    }


def _stop_tree(pid: int) -> None:
    """尽力清理本次明确启动的进程树；仅在采样脚本内部使用。"""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )


def _port_responding(host: str, port: int) -> bool:
    return _http_ready(host, port)


def _wait_stopped(host: str, port: int, pid: int, deadline: float) -> tuple[bool, bool, bool]:
    port_closed = False
    process_gone = False
    children_gone = False
    known_pids = {proc.pid for proc in _tree(pid, _ps_processes_once())}
    while time.monotonic() < deadline:
        port_closed = not _port_responding(host, port)
        current = {p.pid for p in _ps_processes_once()}
        process_gone = pid not in current
        children_gone = not (known_pids & current)
        if port_closed and process_gone and children_gone:
            return True, True, True
        time.sleep(0.15)
    return port_closed, process_gone, children_gone


def _cycle(
    exe: Path,
    config_dir: Path,
    listen: tuple[str, int],
    duration_s: float,
    interval_s: float,
    cycle_no: int,
) -> dict[str, Any]:
    host, port = listen
    started = time.monotonic()
    command = [str(exe), "--port", str(port)]
    env = os.environ.copy()
    env.update(
        {
            "CB_CONFIG_DIR": str(config_dir),
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "MODAL_TOKEN_ID": "",
            "MODAL_TOKEN_SECRET": "",
        }
    )
    proc = subprocess.Popen(
        command,
        cwd=str(exe.parent),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready_deadline = started + STARTUP_TIMEOUT_S
    ready_at = _wait_ready(host, port, ready_deadline)
    if ready_at is None:
        _stop_tree(proc.pid)
        raise RuntimeError(f"第 {cycle_no} 轮：{STARTUP_TIMEOUT_S:.0f}s 内健康接口未就绪")
    cold_ready_ms = round((ready_at - started) * 1000)
    timeline: list[dict[str, Any]] = []
    sample_count = max(1, int(duration_s / interval_s) + 1)
    for index in range(sample_count):
        if index:
            time.sleep(interval_s)
        elapsed = time.monotonic() - ready_at
        timeline.append(_sample_tree(_tree(proc.pid, _ps_processes_once()), started, index, elapsed))

    close_started = time.monotonic()
    _stop_tree(proc.pid)
    port_closed, process_gone, children_gone = _wait_stopped(
        host, port, proc.pid, close_started + CLOSE_TIMEOUT_S
    )
    close_ms = round((time.monotonic() - close_started) * 1000)
    if not process_gone:
        _stop_tree(proc.pid)
    return {
        "cycle": cycle_no,
        "pid": proc.pid,
        "command": command,
        "cold_ready_ms": cold_ready_ms,
        "duration_s": round(time.monotonic() - ready_at, 3),
        "timeline": timeline,
        "close_ms": close_ms,
        "port_closed": port_closed,
        "process_gone": process_gone,
        "children_gone": children_gone,
    }


def _sample_existing(
    pid: int,
    duration_s: float,
    interval_s: float,
    host: str,
    port: int,
) -> dict[str, Any]:
    """采样已启动的后端进程树；不负责停止调用方拥有的服务。"""
    if not _http_ready(host, port):
        raise RuntimeError(f"{host}:{port} 健康接口不可访问")
    started = time.monotonic()
    timeline: list[dict[str, Any]] = []
    sample_count = max(1, int(duration_s / interval_s) + 1)
    for index in range(sample_count):
        if index:
            time.sleep(interval_s)
        elapsed = time.monotonic() - started
        timeline.append(_sample_tree(_tree(pid, _ps_processes_once()), started, index, elapsed))
    if not timeline:
        raise RuntimeError("未采到资源样本")
    if len(timeline) == 1:
        # 资源趋势至少需要两个点；用户显式指定极短采样时仍保留一个点但标记不足。
        pass
    return {
        "cycle": 1,
        "pid": pid,
        "duration_s": round(time.monotonic() - started, 3),
        "timeline": timeline,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="采样桌面整合包启动与空闲资源")
    parser.add_argument("--exe", type=Path, help="整合包 comfy-bridge.exe 路径")
    parser.add_argument("--pid", type=int, help="只采样已有后端进程树（跳过启动/关闭）")
    parser.add_argument("--config-dir", type=Path, default=None, help="隔离配置目录（默认仓库 tmp/desktop-perf-config）")
    parser.add_argument("--listen", type=_parse_listen, default=("127.0.0.1", 18189), help="HOST:PORT")
    parser.add_argument("--cycles", type=int, default=2, help="启动轮数（至少 2 轮才有冷/热基线）")
    parser.add_argument("--duration", type=float, default=15.0, help="每轮就绪后空闲采样秒数")
    parser.add_argument("--interval", type=float, default=3.0, help="资源采样间隔秒数")
    parser.add_argument("--output", type=Path, default=None, help="报告 JSON 路径")
    args = parser.parse_args()
    if args.pid is None and args.exe is None:
        parser.error("需要 --exe 或 --pid")
    if args.pid is None and args.cycles < 2:
        parser.error("--cycles 至少为 2")
    if args.duration <= 0 or args.interval <= 0:
        parser.error("--duration / --interval 必须大于 0")
    if sys.platform != "win32":
        parser.error("当前采样器只支持 Windows（整树资源检查使用系统 API）")
    if args.pid is not None:
        existing_report = {
            "schema_version": 1,
            "mode": "existing-process",
            "generated_at": datetime.now(UTC).isoformat(),
            "host": args.listen[0],
            "port": args.listen[1],
            "cycles": [_sample_existing(args.pid, args.duration, args.interval, *args.listen)],
        }
        output = args.output or ROOT / "tmp" / "desktop-perf.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(existing_report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"WROTE {output}")
        return 0
    assert args.exe is not None
    exe = args.exe.resolve()
    if not exe.is_file():
        parser.error(f"找不到启动器: {exe}")
    config_dir = args.config_dir or ROOT / "tmp" / "desktop-perf-config"
    config_dir = config_dir.resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "exe": str(exe),
        "config_dir": str(config_dir),
        "host": args.listen[0],
        "port": args.listen[1],
        "cycles": [],
    }
    for cycle_no in range(1, args.cycles + 1):
        report["cycles"].append(
            _cycle(exe, config_dir, args.listen, args.duration, args.interval, cycle_no)
        )
    output = args.output or ROOT / "tmp" / "desktop-perf.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

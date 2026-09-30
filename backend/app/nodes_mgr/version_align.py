"""ComfyUI 版本对齐（规划 M2-5 第一片）：本地版本为权威，云端镜像 pin 追随本地。

- 云端 ComfyUI 版本由 modal-runner/lock/comfyui_version.txt 锁定（runner.py 构建镜像
  时读取，缺失回退 runner.py 内默认值）；对齐 = 把本地版本号写入锁文件 → 子进程
  `modal deploy modal-runner/runner.py` 重建镜像（与 volume/billing 完全同一边界，
  后端主进程不 import modal SDK）。
- deploy 失败自动回滚锁文件到旧 pin，避免「pin 改了、镜像没建成」的悬挂态；
  超时属于不确定态（镜像可能仍在远端构建），保留新 pin 并在错误信息里说明。
- 对齐是后台任务（镜像重建需数分钟）：单任务互斥，状态存进程内存，后端重启即清空。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..modal_volume.client import auth_env

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_FILE = REPO_ROOT / "modal-runner" / "runner.py"
LOCK_FILE = REPO_ROOT / "modal-runner" / "lock" / "comfyui_version.txt"
DEFAULT_PIN = "v0.37.4"

_DEPLOY_TIMEOUT = 1800.0        # 镜像重建（torch 层重新安装）实测数分钟，兜底 30 分钟
_LOG_TAIL_LINES = 14
_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')

# 对齐任务状态（单任务互斥；重启即清空，与 comfy_scan 同口径）
_JOB: dict = {
    "state": "idle",            # idle | running | ok | error
    "started_ms": None,
    "finished_ms": None,
    "tag": None,                # 本次对齐的目标 tag（= 本地版本归一化）
    "pinned_before": None,
    "error": None,
    "log_tail": [],
}
_JOB_LOCK = asyncio.Lock()
_TASK: asyncio.Task | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def job_state() -> dict:
    return dict(_JOB, log_tail=list(_JOB["log_tail"]))


def _git_commit(dir_path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(dir_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def local_version(comfy_root: str | Path) -> tuple[str | None, str | None]:
    """本地 ComfyUI 版本与 commit（读 comfyui_version.py，不要求本地服务在线）。"""
    try:
        head = (Path(comfy_root) / "comfyui_version.py").read_text(
            encoding="utf-8", errors="replace"
        )[:400]
        m = _VERSION_RE.search(head)
    except OSError:
        return None, None
    return (m.group(1) if m else None), _git_commit(Path(comfy_root))


def tag_for(version: str) -> str:
    """本地版本号 → GitHub tag："0.38.0" → "v0.38.0"；已带前缀原样返回。"""
    v = (version or "").strip()
    return v if v.startswith("v") else f"v{v}"


def read_pin() -> str:
    try:
        text = LOCK_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_PIN
    return text or DEFAULT_PIN


def write_pin(tag: str) -> None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(tag + "\n", encoding="utf-8")


def status(comfy_root: str | Path) -> dict:
    """本地 vs 云端 pin 对照（纯文件读，决策/UI 轮询安全）。"""
    version, commit = local_version(comfy_root)
    pin = read_pin()
    tag = tag_for(version) if version else None
    return {
        "local_version": version,
        "local_commit": commit,
        "pinned": pin,
        "tag": tag,
        "drift": bool(tag and tag != pin),
        "runner_file": str(RUNNER_FILE),
    }


def _output_tail(out: bytes, err: bytes) -> list[str]:
    lines = [
        *err.decode("utf-8", errors="replace").splitlines(),
        *out.decode("utf-8", errors="replace").splitlines(),
    ]
    keep = [
        ln[:220] for ln in lines
        if ln.strip() and "DeprecationWarning" not in ln and "set_event_loop_policy" not in ln
    ]
    return keep[-_LOG_TAIL_LINES:]


async def _run_deploy() -> None:
    """执行 modal deploy（后台任务体）；非零退出自动回滚 pin，超时保留 pin 并说明。"""
    tag, before = _JOB.get("tag"), _JOB.get("pinned_before")

    def restore_pin() -> None:
        if before:
            try:
                write_pin(before)
            except OSError:
                pass

    try:
        proc = await asyncio.create_subprocess_exec(
            shutil.which("modal") or "modal", "deploy", str(RUNNER_FILE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONWARNINGS": "ignore", **auth_env()},
        )
    except (FileNotFoundError, OSError) as exc:
        restore_pin()
        _JOB.update(state="error", finished_ms=_now_ms(),
                    error=f"无法启动 modal CLI：{exc}", log_tail=[])
        return
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_DEPLOY_TIMEOUT)
    except TimeoutError:
        proc.kill()
        _JOB.update(
            state="error", finished_ms=_now_ms(),
            error=(f"modal deploy 超时（>{_DEPLOY_TIMEOUT:g}s）已终止；远端镜像可能仍在构建，"
                   f"pin 保留为 {tag}，可稍后重新对齐或手动 `modal app list` 核对"),
            log_tail=[f"[超时终止] 目标 tag {tag}"],
        )
        return
    tail = _output_tail(out, err)
    if proc.returncode != 0:
        restore_pin()
        _JOB.update(
            state="error", finished_ms=_now_ms(),
            error=f"modal deploy 失败（退出码 {proc.returncode}），已回滚 pin → {before}",
            log_tail=tail,
        )
        return
    _JOB.update(state="ok", finished_ms=_now_ms(), error=None, log_tail=tail)


async def start_align(comfy_root: str | Path) -> dict:
    """发起一次「云端 → 本地对齐」；无本地版本抛 ValueError，进行中抛 RuntimeError（API 转 4xx/409）。"""
    global _TASK
    version, _ = local_version(comfy_root)
    if not version:
        raise ValueError("未读到本地 ComfyUI 版本（comfy_root 下 comfyui_version.py 缺失或无 __version__）")
    tag = tag_for(version)
    async with _JOB_LOCK:
        if _JOB["state"] == "running":
            raise RuntimeError("版本对齐已在进行中")
        pin = read_pin()
        _JOB.update(
            state="running", started_ms=_now_ms(), finished_ms=None,
            tag=tag, pinned_before=pin, error=None,
            log_tail=[f"对齐目标：云端镜像 pin → {tag}（此前 {pin}）"],
        )
        if tag == pin:
            _JOB.update(
                state="ok", finished_ms=_now_ms(),
                log_tail=[f"云端 pin 已是 {tag}，无需重建；若该 pin 是刚手改的，手动 modal deploy 一次即可生效"],
            )
            return job_state()
        write_pin(tag)
    _TASK = asyncio.create_task(_run_deploy())
    return job_state()

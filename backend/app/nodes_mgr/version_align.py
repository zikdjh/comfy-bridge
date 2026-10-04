"""ComfyUI 版本对齐（规划 M2-5 第一片）：本地版本为权威，云端镜像 pin 追随本地。

- 云端 ComfyUI 版本由 modal-runner/lock/comfyui_version.txt 锁定（runner.py 构建镜像
  时读取，缺失回退 runner.py 内默认值）；对齐 = 把本地版本号写入锁文件 → 子进程
  `modal deploy modal-runner/runner.py` 重建镜像。部署任务体与互斥见 nodes_mgr.deploy
  （与 M2-2 镜像固化共用单飞通道，后端主进程不 import modal SDK）。
- deploy 失败自动回滚锁文件到旧 pin，避免「pin 改了、镜像没建成」的悬挂态；
  超时属不确定态（镜像可能仍在远端构建），保留新 pin 并在错误信息里说明。
- 对齐历史留痕（M2-5 余项 2026-10-03）：每次 pin 变更尝试（含显式对齐与随镜像重建，
  含成功/回滚/超时/无需变更）追加一条 JSONL 到 ~/.comfybridge/version_align_history.jsonl，
  经 deploy 的一次性收尾回调（set_finish_sink）拿到真实结局；留痕失败不影响对齐本身。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .. import runner_assets
from ..core.config import config_dir
from ..modal_volume.client import auth_env  # noqa: F401 —— 保留引用便于测试断言同边界
from . import deploy

RUNNER_FILE = deploy.RUNNER_FILE
LOCK_FILE = runner_assets.runner_file("lock", "comfyui_version.txt")
HISTORY_FILE = config_dir() / "version_align_history.jsonl"
DEFAULT_PIN = "v0.37.4"

_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone().isoformat(timespec="seconds")


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


# ---------------------------------------------------------------- 对齐历史留痕

def _record(trigger: str, from_pin: str, to_tag: str, outcome: str, *,
            error: str | None = None, duration_ms: int | None = None,
            local_commit: str | None = None) -> dict:
    ts = now_ms()
    return {
        "ts_ms": ts, "time": _iso(ts), "trigger": trigger,
        "from_pin": from_pin, "to_tag": to_tag, "outcome": outcome,
        "error": error, "duration_ms": duration_ms, "local_commit": local_commit,
    }


def append_history(record: dict) -> None:
    """追加一条历史（JSONL）；留痕失败（磁盘/权限）静默，不阻塞对齐本身。"""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_history(limit: int = 30) -> list[dict]:
    """最近 limit 条（新的在前）；坏行（手改/半截写入）跳过。"""
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def sink_for(trigger: str, from_pin: str, to_tag: str, started_ms: int,
             local_commit: str | None) -> Callable[[str, str | None], None]:
    """deploy 一次性收尾回调 → 追加一条真实结局的历史（ok/rolled_back/timeout 直取）。"""
    def _on_finish(outcome: str, error: str | None) -> None:
        append_history(_record(
            trigger, from_pin, to_tag, outcome, error=error,
            duration_ms=max(now_ms() - started_ms, 0), local_commit=local_commit,
        ))

    return _on_finish


async def start_align(comfy_root: str | Path) -> dict:
    """发起一次「云端 → 本地对齐」；无本地版本抛 ValueError，部署进行中抛 RuntimeError（API 转 4xx/409）。"""
    version, commit = local_version(comfy_root)
    if not version:
        raise ValueError("未读到本地 ComfyUI 版本（comfy_root 下 comfyui_version.py 缺失或无 __version__）")
    if deploy.running():
        raise RuntimeError("已有镜像部署任务在进行中")
    tag = tag_for(version)
    pin = read_pin()
    if tag == pin:
        # 无需重建也留痕（用户尝试过对齐）；finish_noop 不经 deploy 收尾，这里直接记
        append_history(_record("align", pin, tag, "noop"))
        return deploy.finish_noop(
            f"云端 pin 已是 {tag}，无需重建；若该 pin 是刚手改的，手动 modal deploy 一次即可生效"
        )
    started_ms = now_ms()
    await deploy.begin_exclusive(f"版本对齐 → {tag}（此前 {pin}）")
    write_pin(tag)

    def restore_pin() -> None:
        try:
            write_pin(pin)
        except OSError:
            pass

    deploy.set_finish_sink(sink_for("align", pin, tag, started_ms, commit))
    deploy.spawn(restore_pin)
    return deploy.job_state()

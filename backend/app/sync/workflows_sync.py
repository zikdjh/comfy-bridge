"""工作流双向同步（M2-4）：本地 workflows 目录 ⇄ 数据卷 user/default/workflows/。

卷访问与节点/设置同步同一边界（modal CLI 子进程，免容器读）；检测逻辑在
workflows_rules（纯函数，单测覆盖）。冲突处理按规划「保留双副本 + UI 人工合并」：
keep_both 把云端版拉成本地 x.conflict-<ts>.json，再把本地版推上云端，基线记本地版。

v1 边界：删除不传播——仅本地存在的文件会重新推回云端（恢复），云端删掉本地还在的
工作流下次会「复活」；语义上对「云端卷被误清」是保护，对「故意删旧稿」需在本地删。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from ..core.config import modal_configured, workflows_dir
from ..modal_volume import client as volume_cli
from ..modal_volume.client import ModalCliError
from . import jobs
from . import workflows_rules as rules

CLOUD_DIR = "user/default/workflows"
STATE_FILE = Path.home() / ".comfybridge" / "workflows_state.json"

_APPLY_LOCK = asyncio.Lock()


def _sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def local_hashes() -> dict[str, str]:
    """本地目录的 name→sha256（只看一层 *.json；非法名跳过）。"""
    out: dict[str, str] = {}
    root = workflows_dir()
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.json")):
        if not rules.valid_workflow_name(p.name):
            continue
        digest = _sha256(p)
        if digest:
            out[p.name] = digest
    return out


def read_state() -> dict[str, str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def write_state(state: dict[str, str]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        pass  # 基线写失败只影响下次变更判定，不阻塞同步本身


async def cloud_hashes() -> tuple[dict[str, str], str | None]:
    """云端目录的 name→sha256；目录不存在（未同步过）视为空。返回 (hashes, error)。"""
    try:
        entries = await volume_cli.list_dir(CLOUD_DIR)
    except ModalCliError as exc:
        if "No such file or directory" in str(exc):
            return {}, None
        return {}, str(exc)
    files = [e["name"] for e in entries if e.get("type") not in ("dir", "directory")]
    hashes: dict[str, str] = {}
    tmp_root = Path(tempfile.mkdtemp(prefix="cb-wf-sync-"))
    try:
        for name in files:
            if not rules.valid_workflow_name(name):
                continue
            dest = tmp_root / name
            await volume_cli.run("volume", "get", volume_cli.volume_name(), f"{CLOUD_DIR}/{name}", str(dest))
            digest = _sha256(dest)
            if digest:
                hashes[name] = digest
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return hashes, None


async def status() -> dict:
    """同步矩阵：本地/云端 hash、三方检测行、基线说明。"""
    local = await asyncio.to_thread(local_hashes)
    cloud, error = await cloud_hashes()
    base = await asyncio.to_thread(read_state)
    rows = rules.classify_rows(local, cloud, base)
    if not error:
        base = rules.merge_state(base, rows)
        await asyncio.to_thread(write_state, base)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "ok": error is None,
        "error": error,
        "local_dir": str(workflows_dir()),
        "cloud_dir": CLOUD_DIR,
        "volume": volume_cli.volume_name(),
        "rows": rows,
        "counts": counts,
    }


async def _volume_put(local_path: Path, remote: str) -> None:
    await volume_cli.run("volume", "put", volume_cli.volume_name(), str(local_path), remote, "-f")


async def _pull_to(name: str, dest: Path) -> None:
    # --force：pull 的目标（本地工作流文件）通常已存在，CLI 默认拒绝覆盖
    await volume_cli.run("volume", "get", volume_cli.volume_name(), f"{CLOUD_DIR}/{name}", str(dest), "--force")


async def apply_as_job(actions: list[dict]) -> dict:
    """同步动作任务化（M2-6）：预检失败直接返回错误不开作业；否则立即返回 job，
    逐条执行进度经 JobHandle 汇报（前端 JobDrawer 轮询跟踪，可中途取消）。"""
    if not modal_configured():
        return {"ok": False, "error": "未配置 Modal 凭证（设置 → Modal）", "results": []}
    root = workflows_dir()
    if not root.is_dir():
        return {"ok": False, "error": f"本地工作流目录不存在：{root}", "results": []}
    if not actions:
        return {"ok": False, "error": "没有要执行的动作", "results": []}
    job = jobs.start_job(
        "workflow_sync", f"工作流同步（{len(actions)} 项）", len(actions),
        lambda handle: _apply_body(handle, actions), cancellable=True,
    )
    return {"ok": True, "job": job}


async def _apply_body(handle, actions: list[dict]) -> None:
    """作业体：逐条执行 push/pull/keep_both 并推进作业条目；基线随成功项推进。"""
    root = workflows_dir()
    async with _APPLY_LOCK:
        local = await asyncio.to_thread(local_hashes)
        cloud, error = await cloud_hashes()
        if error:
            raise RuntimeError(f"云端清单读取失败：{error}")
        state = await asyncio.to_thread(read_state)
        for action in actions:
            handle.check_cancel()
            name, op = str(action.get("name", "")), str(action.get("action", ""))
            if not rules.valid_workflow_name(name):
                result: dict = {"name": name, "ok": False, "action": op, "error": "非法文件名"}
            else:
                lh, ch = local.get(name), cloud.get(name)
                result = {"name": name, "ok": False, "action": op}
                try:
                    if op == rules.PUSH:
                        if lh is None:
                            result["error"] = "本地不存在"
                        else:
                            await _volume_put(root / name, f"{CLOUD_DIR}/{name}")
                            state[name] = lh
                            result["ok"] = True
                    elif op == rules.PULL:
                        if ch is None:
                            result["error"] = "云端不存在"
                        else:
                            await _pull_to(name, root / name)
                            state[name] = ch
                            result["ok"] = True
                    elif op == rules.KEEP_BOTH:
                        if ch is None or lh is None:
                            result["error"] = "冲突双副本需要两端都有文件"
                        else:
                            ts = time.strftime("%Y%m%d-%H%M%S")
                            copy_name = rules.conflict_copy_name(name, ts)
                            tmp_root = Path(tempfile.mkdtemp(prefix="cb-wf-conflict-"))
                            try:
                                cloud_copy = tmp_root / copy_name
                                await _pull_to(name, cloud_copy)
                                shutil.move(str(cloud_copy), str(root / copy_name))
                            finally:
                                shutil.rmtree(tmp_root, ignore_errors=True)
                            await _volume_put(root / name, f"{CLOUD_DIR}/{name}")
                            state[name] = lh
                            result["ok"] = True
                            result["conflict_copy"] = copy_name
                    else:
                        result["error"] = f"未知动作 {op!r}"
                except ModalCliError as exc:
                    result["error"] = str(exc)[:300]
            handle.advance(result)
            handle.log(f"{op} {name} → {'完成' if result['ok'] else result.get('error', '失败')}")
        await asyncio.to_thread(write_state, state)

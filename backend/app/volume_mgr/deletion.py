"""模型删除保护：引用检查与回收站语义。

卷删除本身由 ``/volume/delete`` 提供；本模块只处理“被模型引用的条目”
和“可恢复删除”。引用来源按规划拆成三类：

- ``requests`` 表：请求记录的主模型字段（``model_name``）；
- ``data/graphs/*.json``：历史请求图快照（模型加载节点的输入值）；
- 本地工作流目录：用户当前保存的工作流（文件名 + 图内模型引用）。

回收站路径固定为卷内 ``.trash/<UTC 时间戳>/<原路径>``。恢复只允许从
``.trash/...`` 回到卷内；永久清理只允许删除 ``.trash`` 下的精确路径，
并且不会递归删除 ``.trash`` 本身或非回收站路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.config import workflows_dir
from ..core.constants import MODEL_FILE_EXTS, MODEL_INPUT_CATEGORIES
from ..logs import store
from ..modal_volume import client as modal_cli
from ..modal_volume.client import ModalCliError

TRASH_ROOT = ".trash"
TRASH_RETENTION_DAYS = 7
_TRASH_STAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)$")
_TRASH_RE = re.compile(r"^\.trash/(\d{8}T\d{6}Z)/")
logger = logging.getLogger(__name__)


def is_model_path(rel: str) -> bool:
    """判断卷内路径是否属于模型文件（models/<分类>/...）。"""
    rel = (rel or "").replace("\\", "/").strip("/")
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] != "models":
        return False
    return Path(parts[-1]).suffix.lower() in MODEL_FILE_EXTS


def model_refs(prompt: dict | Any) -> set[str]:
    """从图/工作流中提取模型引用值（复用预校验的输入名判定）。"""
    if not isinstance(prompt, dict):
        return set()
    refs: set[str] = set()
    nodes = prompt.get("nodes")
    if not isinstance(nodes, list):
        nodes = list(prompt.values())
    for node in nodes:
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            # ComfyUI 工作流导出格式把输入放在 widgets_values 里；只做保守文本兜底。
            widgets = node.get("widgets_values")
            if isinstance(widgets, list):
                for value in widgets:
                    if isinstance(value, str) and Path(value).suffix.lower() in MODEL_FILE_EXTS:
                        refs.add(value)
            continue
        for key, value in inputs.items():
            if not isinstance(value, str) or not value or value == "None":
                continue
            if key in MODEL_INPUT_CATEGORIES or (
                key.endswith("_name") and Path(value).suffix.lower() in MODEL_FILE_EXTS
            ):
                refs.add(value)
    return refs


def _graph_refs() -> list[dict]:
    """扫描最近保留的图快照，返回 [{source, ref, detail}]。"""
    out: list[dict] = []
    root = store.GRAPH_DIR
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for ref in sorted(model_refs(payload)):
            out.append({
                "source": "graph",
                "ref": ref,
                "detail": path.name,
            })
    return out


def _workflow_refs() -> list[dict]:
    """扫描本地工作流目录，返回 [{source, ref, detail}]。"""
    out: list[dict] = []
    root = workflows_dir()
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for ref in sorted(model_refs(payload)):
            out.append({
                "source": "workflow",
                "ref": ref,
                "detail": path.name,
            })
    return out


def _request_refs(limit: int = 2000) -> list[dict]:
    """扫描请求记录里的主模型字段。"""
    rows = store.list_requests(limit=limit)
    out: list[dict] = []
    for row in rows:
        ref = (row.get("model_name") or "").strip()
        if not ref:
            continue
        out.append({
            "source": "request",
            "ref": ref,
            "detail": f"请求 #{row.get('id')} · {row.get('created_at') or ''}",
        })
    return out


def _matches(ref: str, target: str) -> bool:
    """模型引用匹配：同时接受裸文件名和分类内相对路径。"""
    ref = (ref or "").replace("\\", "/").strip()
    target = (target or "").replace("\\", "/").strip()
    if not ref or not target:
        return False
    return ref == target or ref.rsplit("/", 1)[-1] == target.rsplit("/", 1)[-1]


def inspect_references(rel: str, *, request_limit: int = 2000) -> dict:
    """检查一个卷内模型路径是否被请求/图/工作流引用。

    返回 ``{"checked": bool, ...}``；非模型路径不扫描，避免目录浏览的每次
    删除都去读日志库和工作流目录。
    """
    rel = (rel or "").replace("\\", "/").strip("/")
    if not is_model_path(rel):
        return {"checked": False, "referenced": False, "refs": [], "warning": None}

    candidates = _request_refs(request_limit) + _graph_refs() + _workflow_refs()
    hits: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        if not _matches(str(candidate.get("ref") or ""), rel):
            continue
        key = (str(candidate.get("source")), str(candidate.get("ref")), str(candidate.get("detail")))
        if key in seen:
            continue
        seen.add(key)
        hits.append(candidate)
    warning = None
    if hits:
        warning = (
            f"{rel} 被 {len(hits)} 条近期请求、图快照或工作流引用；"
            "确认后会用回收站软删除，可在回收站恢复。"
        )
    return {
        "checked": True,
        "referenced": bool(hits),
        "refs": hits[:100],
        "truncated": len(hits) > 100,
        "warning": warning,
    }


def trash_rel(now: datetime | None = None, rel: str = "") -> str:
    """生成回收站目标路径（固定 UTC 时间戳目录）。"""
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{TRASH_ROOT}/{ts}/{rel.replace(chr(92), '/').strip('/')}"


def is_trash_path(rel: str) -> bool:
    return bool(_TRASH_RE.match((rel or "").replace("\\", "/").strip("/")))


def trash_stamp_age_days(name: str, *, now: datetime | None = None) -> float | None:
    """返回时间戳目录的年龄（天）；非本模块格式返回 None。"""
    match = _TRASH_STAMP_RE.match((name or "").strip())
    if not match:
        return None
    try:
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return ((now or datetime.now(UTC)) - stamp).total_seconds() / 86400


async def move_to_trash(volume: str, rel: str, kind: str = "file") -> str:
    """把卷内条目软删除到 .trash；返回目标路径。"""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith(f"{TRASH_ROOT}/") or rel == TRASH_ROOT:
        raise ValueError("不能把回收站本身移入回收站")
    dest = trash_rel(rel=rel)
    await move_entry(volume, rel, dest, kind)
    return dest


async def restore_from_trash(
    volume: str, rel: str, dest: str | None = None, kind: str = "file",
) -> str:
    """从回收站恢复到原路径或指定卷内路径。"""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not is_trash_path(rel):
        raise ValueError("只能从 .trash/<时间戳>/... 恢复")
    target = (dest or "").replace("\\", "/").strip("/")
    if not target:
        target = rel.split("/", 2)[2]
    if not target or target == TRASH_ROOT or target.startswith(f"{TRASH_ROOT}/"):
        raise ValueError("恢复目标必须是回收站之外的卷内相对路径")
    await move_entry(volume, rel, target, kind)
    return target


async def move_entry(volume: str, src: str, dest: str, kind: str = "file") -> None:
    """经 volume_ops.py 在云端容器内复制 + 删源（适用于目录与文件）。"""
    from ..runner_assets import runner_file

    ops_file = runner_file("volume_ops.py")
    if not ops_file.is_file():
        raise RuntimeError(f"找不到 volume_ops 脚本: {ops_file}")
    kind = "dir" if kind in ("dir", "directory") else "file"
    payload = json.dumps([[src, dest, kind]])
    proc = modal_cli._popen(
        "run", f"{ops_file}::move_entries",
        "--entries-json", payload,
        extra_env={"CB_MODAL_VOLUME": volume},
    )
    out, err = await asyncio.to_thread(proc.communicate)
    if proc.returncode != 0:
        raise ModalCliError((err or out).decode("utf-8", errors="replace").strip()[:400])
    raw = out.decode("utf-8", errors="replace")
    marker = raw.find('{"results"')
    if marker >= 0:
        try:
            payload_out = json.loads(raw[marker:])
        except ValueError:
            payload_out = None
        if isinstance(payload_out, dict) and not payload_out.get("ok", False):
            results = payload_out.get("results") or []
            first = next((str(r.get("error")) for r in results if not r.get("ok")), "移动失败")
            raise ModalCliError(first[:400])


async def list_trash(volume: str) -> list[dict]:
    """列出回收站一级时间戳目录及其中条目。"""
    try:
        roots = await modal_cli.list_dir(TRASH_ROOT, volume=volume)
    except ModalCliError as exc:
        if "No such file or directory" in str(exc):
            return []
        raise
    out: list[dict] = []
    for root in roots:
        if root.get("type") not in ("dir", "directory"):
            continue
        ts_dir = f"{TRASH_ROOT}/{root['name']}"
        try:
            entries = await modal_cli.list_dir(ts_dir, volume=volume)
        except ModalCliError as exc:
            out.append({
                "path": ts_dir,
                "name": root["name"],
                "type": "dir",
                "size": root.get("size", ""),
                "mtime": root.get("mtime", ""),
                "error": str(exc)[:200],
            })
            continue
        for entry in entries:
            out.append({
                "path": f"{ts_dir}/{entry['name']}",
                "name": entry["name"],
                "type": entry.get("type", "file"),
                "size": entry.get("size", ""),
                "mtime": entry.get("mtime", ""),
                "error": None,
            })
    return out


async def cleanup_expired_trash(
    volume: str,
    *,
    retention_days: int = TRASH_RETENTION_DAYS,
    now: datetime | None = None,
) -> list[str]:
    """删除超过保留期的时间戳目录；只允许命中 .trash/<合法时间戳>。"""
    try:
        roots = await modal_cli.list_dir(TRASH_ROOT, volume=volume)
    except ModalCliError as exc:
        if "No such file or directory" in str(exc):
            return []
        raise
    removed: list[str] = []
    for root in roots:
        name = str(root.get("name") or "")
        age = trash_stamp_age_days(name, now=now)
        if age is None or age < retention_days:
            continue
        rel = f"{TRASH_ROOT}/{name}"
        await modal_cli.run("volume", "rm", volume, rel, "-r")
        removed.append(rel)
        logger.info("回收站清理：卷 %s 删除过期条目 %s（%.1f 天）", volume, rel, age)
    return removed


async def cleanup_all_expired_trash(retention_days: int = TRASH_RETENTION_DAYS) -> dict[str, list[str]]:
    """清理所有卷的过期回收站；单卷失败不阻断其余卷。"""
    out: dict[str, list[str]] = {}
    if not modal_cli.cli_path():
        # 安装态仅本地桥接（未装 modal CLI）属正常形态，不作为启动告警刷屏。
        logger.debug("回收站清理：modal CLI 未安装，跳过云端回收站清理")
        return out
    try:
        rows = modal_cli.cli_json_list(await modal_cli.run("volume", "list", "--json"))
    except Exception as exc:  # noqa: BLE001 —— 后台清理必须静默降级
        logger.warning("回收站清理：无法列出数据卷: %s", exc)
        return out
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        try:
            out[name] = await cleanup_expired_trash(name, retention_days=retention_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("回收站清理：卷 %s 失败: %s", name, exc)
    return out

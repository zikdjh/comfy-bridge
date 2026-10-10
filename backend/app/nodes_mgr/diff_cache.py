"""节点差异矩阵持久化缓存（M2-1 余项）。

~/.comfybridge/nodes_diff_cache.json 两个独立区块：
- matrix：/nodes/diff 的非 deep 全量响应（rows/counts/manifest/volume/ok/cloud_error）
  —— 短 TTL 保鲜；同步/移除节点后主动失效，失败结果不缓存
- class_types：deep=1 的两端 class_type 对比块 —— 无 TTL 长期持久化，
  只有 force=1 或缺失才重拉（避免每次进页面都重新触发云端冷启动计费）

时间戳用 wall clock 毫秒（新鲜度判定须跨进程成立），对外输出 ISO 秒级时间戳供前端展示。
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from ..core.config import config_dir

logger = logging.getLogger(__name__)

_TTL_MS = 120_000
_SCHEMA = 1
_doc: dict | None = None


def _path() -> Path:
    return config_dir() / "nodes_diff_cache.json"


def _load() -> dict:
    global _doc
    if _doc is None:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            _doc = data if isinstance(data, dict) and data.get("schema") == _SCHEMA else {"schema": _SCHEMA}
        except Exception:  # noqa: BLE001 —— 缺文件/坏文件等同空缓存
            _doc = {"schema": _SCHEMA}
    return _doc


def _save() -> None:
    if _doc is None:
        return
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_doc, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        # 缓存落盘失败不影响请求本身，下次现算即可
        logger.warning("节点差异缓存落盘失败: %s", exc)


def _iso(ms: float | None) -> str | None:
    if not ms:
        return None
    # UTC 存储时间戳转本地时区展示（缓存时刻给前端看的口径）
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone().isoformat(timespec="seconds")


def load_matrix(max_age_ms: float = _TTL_MS) -> dict | None:
    """TTL 内的矩阵缓存；命中时注入 cached/cached_at 并返回副本。"""
    doc = _load()
    matrix = doc.get("matrix")
    ms = doc.get("matrix_ms") or 0
    if not isinstance(matrix, dict) or not matrix:
        return None
    if time.time() * 1000 - ms > max_age_ms:
        return None
    out = copy.deepcopy(matrix)
    out["cached"] = True
    out["cached_at"] = _iso(ms)
    return out


def save_matrix(payload: dict) -> None:
    """只缓存成功计算（ok=True）的矩阵；app_state 属于运行时状态，不进缓存（每次现取）。"""
    if not payload.get("ok"):
        return
    doc = _load()
    doc["matrix"] = {k: v for k, v in payload.items() if k != "app_state"}
    doc["matrix_ms"] = time.time() * 1000
    _save()


def load_class_types() -> dict | None:
    """deep class_type 块（长期有效）；命中时注入 cached/cached_at 并返回副本。"""
    doc = _load()
    ct = doc.get("class_types")
    ms = doc.get("class_types_ms") or 0
    if not isinstance(ct, dict):
        return None
    out = copy.deepcopy(ct)
    out["cached"] = True
    out["cached_at"] = _iso(ms)
    return out


def save_class_types(ct: dict) -> None:
    doc = _load()
    doc["class_types"] = {
        "local": ct.get("local"),
        "cloud": ct.get("cloud"),
        "error": ct.get("error"),
        "conflicts": ct.get("conflicts") or {},
        "merge_strategy": ct.get("merge_strategy"),
    }
    doc["class_types_ms"] = time.time() * 1000
    _save()


def load_cloud_schemas() -> dict:
    """deep 对比时缓存的云端完整 object_info；缺失或损坏时返回空字典。"""
    doc = _load()
    schemas = doc.get("cloud_schemas")
    return copy.deepcopy(schemas) if isinstance(schemas, dict) else {}


def save_cloud_schemas(schemas: dict) -> None:
    """持久化云端完整 schema，供后端重启后 /object_info 合并复用。"""
    doc = _load()
    doc["cloud_schemas"] = schemas if isinstance(schemas, dict) else {}
    doc["cloud_schemas_ms"] = time.time() * 1000
    _save()


def invalidate_matrix() -> None:
    """矩阵区块失效（同步/移除节点后调用）；class_types 与实际安装的 class 无关，保留。"""
    doc = _load()
    doc.pop("matrix", None)
    doc.pop("matrix_ms", None)
    _save()

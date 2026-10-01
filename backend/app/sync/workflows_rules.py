"""工作流双向同步纯逻辑（M2-4）：三方变更检测与状态分类。

本地 user/default/workflows/ ⇄ 数据卷 user/default/workflows/，按文件同步。
变更检测用 last-synced 基线 hash（存桥接器自己的状态文件，不污染 ComfyUI 目录——
目录里的隐藏 .json 仍会被 ComfyUI 当工作流列出）：

- 仅本地 → local_new（动作 push）；仅云端 → cloud_new（动作 pull）
- 两端 hash 一致 → synced（顺带刷新基线）
- 不一致看基线：基线==云端 → 本地改 → local_changed（push）；
  基线==本地 → 云端改 → cloud_changed（pull）；
  基线缺失或与两端都不同 → conflict（两端都改过/无基线可判，人工三选）
"""

from __future__ import annotations

PUSH = "push"
PULL = "pull"
KEEP_BOTH = "keep_both"

S_SYNCED = "synced"
S_LOCAL_NEW = "local_new"
S_CLOUD_NEW = "cloud_new"
S_LOCAL_CHANGED = "local_changed"
S_CLOUD_CHANGED = "cloud_changed"
S_CONFLICT = "conflict"

ACTION_FOR: dict[str, str | None] = {
    S_SYNCED: None,
    S_LOCAL_NEW: PUSH,
    S_CLOUD_NEW: PULL,
    S_LOCAL_CHANGED: PUSH,
    S_CLOUD_CHANGED: PULL,
    S_CONFLICT: None,  # 人工决定：push / pull / keep_both
}


def valid_workflow_name(name: str) -> bool:
    """文件名白名单校验：一层、无隐藏/穿越段、必须 .json 结尾。"""
    return (
        bool(name)
        and name.endswith(".json")
        and not name.startswith(".")
        and "/" not in name
        and "\\" not in name
        and ".." not in name
    )


def conflict_copy_name(name: str, ts: str) -> str:
    """冲突双副本的本地文件名：x.json → x.conflict-<ts>.json（ts 如 20260930-104500）。"""
    stem = name.removesuffix(".json")
    return f"{stem}.conflict-{ts}.json"


def classify_rows(
    local: dict[str, str], cloud: dict[str, str], base: dict[str, str]
) -> list[dict]:
    """三方检测：local/cloud 为 name→sha256，base 为 name→上次同步时的 hash。

    返回按文件名排序的行：name / state / suggested 动作 / local_hash / cloud_hash。
    """
    rows: list[dict] = []
    for name in sorted(set(local) | set(cloud)):
        lh, ch = local.get(name), cloud.get(name)
        if ch is None:
            state = S_LOCAL_NEW
        elif lh is None:
            state = S_CLOUD_NEW
        elif lh == ch:
            state = S_SYNCED
        else:
            b = base.get(name)
            if b is not None and b == ch:
                state = S_LOCAL_CHANGED   # 上次同步后只有本地动过
            elif b is not None and b == lh:
                state = S_CLOUD_CHANGED   # 上次同步后只有云端动过
            else:
                state = S_CONFLICT        # 无基线（首次对比）或两边都动过
        rows.append({
            "name": name,
            "state": state,
            "suggested": ACTION_FOR[state],
            "local_hash": lh,
            "cloud_hash": ch,
        })
    return rows


def merge_state(base: dict[str, str], rows: list[dict]) -> dict[str, str]:
    """状态查询顺带刷新基线：synced 行以当前 hash 记为基线，其余行不动。"""
    merged = dict(base)
    for row in rows:
        if row["state"] == S_SYNCED and row["local_hash"]:
            merged[row["name"]] = row["local_hash"]
    return merged

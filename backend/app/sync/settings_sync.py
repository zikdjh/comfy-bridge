"""设置键级推拉（M2-5）纯逻辑：键级 diff、按方向合并、敏感键掩码。

方向语义（计划书 M2-5「settings 键级推拉」）：
- push：把本地值写入云端（云端文件随 volume put 落卷，容器下次冷启动生效）
- pull：把云端值写入本地（本地 ComfyUI 下次读设置时生效）
- 未出现在 directions 中的键保持原值；推送/拉取不到的键计入 skipped，不静默丢弃。
"""

from __future__ import annotations

from collections.abc import Mapping

# 键名含这些片段（不区分大小写）的值视为凭证类：API 回显一律掩码（红线），推拉仍用真实值
SENSITIVE_KEY_FRAGMENTS = ("api_key", "apikey", "token", "secret", "password")

PUSH = "push"
PULL = "pull"

# status 取值：same 两端一致 / diff 两端都有但不同 / only_local 云端缺 / only_cloud 本地缺
STATUS_SAME = "same"
STATUS_DIFF = "diff"
STATUS_ONLY_LOCAL = "only_local"
STATUS_ONLY_CLOUD = "only_cloud"

DEFAULT_DIRECTION = {STATUS_DIFF: PUSH, STATUS_ONLY_LOCAL: PUSH, STATUS_ONLY_CLOUD: PULL, STATUS_SAME: None}


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(frag in lowered for frag in SENSITIVE_KEY_FRAGMENTS)


def mask_secret(key: str, value) -> str | None:
    """凭证类键的显示值：first5+****+last4（过短全掩码）；非凭证键原样字符串化。

    缺失/空值返回空串——没有值就无可掩码，不伪装成「有隐藏值」。
    布尔按 JSON 风格渲染（true/false），与文件内容一致，避免 Python 的 True/False 混入 UI。
    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = "" if value is None else str(value)
    if not is_sensitive_key(key):
        return text
    if not text:
        return ""
    if len(text) >= 12:
        return f"{text[:5]}****{text[-4:]}"
    return "****"


def diff_settings(local: dict, cloud: dict) -> list[dict]:
    """键级差异行（真实值 + sensitive 标记，掩码由显示层做）；键名排序保证稳定输出。"""
    rows: list[dict] = []
    for key in sorted(set(local) | set(cloud)):
        lv = local.get(key)
        cv = cloud.get(key)
        if key not in cloud:
            status = STATUS_ONLY_LOCAL
        elif key not in local:
            status = STATUS_ONLY_CLOUD
        elif lv != cv:
            status = STATUS_DIFF
        else:
            status = STATUS_SAME
        rows.append({
            "key": key,
            "local": lv,
            "cloud": cv,
            "status": status,
            "sensitive": is_sensitive_key(key),
            "default_direction": DEFAULT_DIRECTION[status],
        })
    return rows


def apply_directions(
    local: dict, cloud: dict, directions: Mapping[str, str]
) -> tuple[dict, dict, list[str], list[str], list[str]]:
    """按 {键: push|pull} 合并两端设置，返回 (new_local, new_cloud, pushed, pulled, skipped)。

    push 要求键存在于本地，pull 要求键存在于云端，否则计入 skipped；未知方向同样跳过。
    """
    new_local = dict(local)
    new_cloud = dict(cloud)
    pushed: list[str] = []
    pulled: list[str] = []
    skipped: list[str] = []
    for key, direction in directions.items():
        if direction == PUSH:
            if key not in local:
                skipped.append(key)
                continue
            new_cloud[key] = local[key]
            pushed.append(key)
        elif direction == PULL:
            if key not in cloud:
                skipped.append(key)
                continue
            new_local[key] = cloud[key]
            pulled.append(key)
        else:
            skipped.append(key)
    return new_local, new_cloud, pushed, pulled, skipped

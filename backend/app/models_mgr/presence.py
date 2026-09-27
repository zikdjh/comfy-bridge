"""模型引用归属判定：图里引用的模型在本地 / 云端（Modal 数据卷）的存在情况。

两侧索引均为「分类 → 名称集合」，名称含相对路径与裸文件名两种形态；
展平后按精确匹配判定（索引构建时已做双形态归一）。
"""

from __future__ import annotations


def _flat(index: dict[str, set[str]]) -> set[str]:
    if not index:
        return set()
    return set().union(*index.values())


def classify_model_refs(
    refs: list[str],
    local: dict[str, set[str]],
    cloud: dict[str, set[str]],
) -> tuple[list[str], list[str]]:
    """返回 (cloud_only, missing)：

    - cloud_only：本地没有、云端有 → 路由必须走 Modal（本地校验必然拒绝）
    - missing：两侧都没有 → 仍交本地执行，由 ComfyUI 给出权威的校验错误
    """
    local_flat = _flat(local)
    cloud_flat = _flat(cloud)
    cloud_only: list[str] = []
    missing: list[str] = []
    for ref in refs:
        if ref in local_flat:
            continue
        if ref in cloud_flat:
            cloud_only.append(ref)
        else:
            missing.append(ref)
    return cloud_only, missing

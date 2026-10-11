"""节点依赖重建前预检（P1-3）。

镜像重建会把各节点的 ``requirements.txt`` 顺序交给 pip；冲突通常要等远端构建
失败后才暴露。这里在写 ``nodes.lock.yaml`` 之前做两层检查：

1. 静态审计：按目标镜像环境（Linux / Python 3.12）评估 marker，按规范化包名
   归并约束，并识别确定没有交集的版本范围。
2. 真实解析：调用当前解释器的 pip dry-run resolver 检查传递依赖。只有明确的
   ``ResolutionImpossible`` 才阻断；本机平台、Python 版本、网络或超时问题均降级
   为 ``unavailable``，不把它误判成目标镜像中的冲突。

预检结果状态：

* ``ok``：可以继续镜像重建。
* ``blocked``：确定存在依赖冲突，取消重建并返回涉及的节点和约束。
* ``unavailable``：无法在本机完成权威解析，继续进入远端 deploy，由现有失败回滚
  路径兜底。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

_RESOLVE_TIMEOUT = 240.0
_CACHE_TTL_SECONDS = 300.0

# 目标镜像按 Modal runner 的 Linux / Python 3.12 环境评估 marker；本机可能是
# Windows / Python 3.14，不能直接使用 packaging 默认环境。
_TARGET_ENV = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.0",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Linux",
    "platform_version": "",
    "python_full_version": "3.12.0",
    "python_version": "3.12",
    "sys_platform": "linux",
    "extra": "",
}

_PIP_CACHE: dict[tuple[str, ...], tuple[float, tuple[str, str | None, list[str]]]] = {}
_PIP_CACHE_LOCK = threading.Lock()


@dataclass
class Conflict:
    package: str
    requirements: list[str] = field(default_factory=list)
    reason: str = ""
    nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "requirements": self.requirements,
            "reason": self.reason,
            "nodes": self.nodes,
        }


@dataclass
class _Item:
    raw: str
    node: str
    req: Requirement | None = None


class DependencyConflictError(RuntimeError):
    """镜像重建前预检确认存在依赖冲突。"""

    def __init__(self, result: dict):
        super().__init__(blocked_message(result))
        self.result = result


def clear_cache() -> None:
    """清空 pip 结果内存缓存（测试与显式刷新使用）。"""
    with _PIP_CACHE_LOCK:
        _PIP_CACHE.clear()


def _collect(nodes: list[dict]) -> tuple[list[_Item], list[Conflict]]:
    items: list[_Item] = []
    errors: list[Conflict] = []
    for node in nodes:
        name = str(node.get("name") or "（未命名）")
        for raw in node.get("requirements") or []:
            text = str(raw).strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("-"):
                # pip 选项（如 --extra-index-url URL）原样交给真实 resolver；
                # 不能按包名归并，也不做语法判断。
                items.append(_Item(raw=text, node=name))
                continue
            try:
                req = Requirement(text)
            except InvalidRequirement as exc:
                if _looks_like_direct_reference(text):
                    if _direct_marker_applies(text):
                        items.append(_Item(raw=_strip_direct_marker(text), node=name))
                    continue
                errors.append(Conflict(
                    package=_guess_package(text),
                    requirements=[text],
                    reason=f"依赖声明无法解析：{exc}",
                    nodes=[name],
                ))
                continue
            if req.marker is not None and not req.marker.evaluate(_TARGET_ENV):
                continue
            items.append(_Item(raw=text, node=name, req=req))
    return items, errors


def _guess_package(text: str) -> str:
    head = text.split(";", 1)[0].strip()
    for sep in ("[", "==", "===", ">=", "<=", "~=", "!=", ">", "<", "="):
        head = head.split(sep, 1)[0]
    return head.strip() or text


def _looks_like_direct_reference(text: str) -> bool:
    """判断 pip requirements.txt 接受、但不属于 PEP 508 name 语法的直接引用。"""
    base = _requirement_base(text).lower()
    if base.startswith(("git+", "hg+", "svn+", "bzr+", "file:")):
        return True
    if "://" in base or "#egg=" in base:
        return True
    if base.startswith((".", "/", "~")):
        return True
    if len(base) > 1 and base[1] == ":" and base[0].isalpha():
        return True
    return base.endswith((".whl", ".tar.gz", ".tar.bz2", ".tar.xz", ".zip"))


def _requirement_base(text: str) -> str:
    """返回分号前的依赖主体；只有后缀是合法 marker 时才剥离。"""
    base, sep, marker_text = text.rpartition(";")
    if not sep or not base.strip():
        return text
    try:
        Marker(marker_text.strip())
    except InvalidMarker:
        return text
    return base.strip()


def _direct_marker_applies(text: str) -> bool:
    base, sep, marker_text = text.rpartition(";")
    if not sep or not base.strip():
        return True
    try:
        marker = Marker(marker_text.strip())
    except InvalidMarker:
        return True
    return marker.evaluate(_TARGET_ENV)


def _strip_direct_marker(text: str) -> str:
    return _requirement_base(text)


def _direct_conflicts(items: list[_Item]) -> list[Conflict]:
    grouped: dict[str, list[_Item]] = {}
    for item in items:
        if item.req is not None:
            grouped.setdefault(canonicalize_name(item.req.name), []).append(item)

    conflicts: list[Conflict] = []
    for package, group in sorted(grouped.items()):
        spec = SpecifierSet(",".join(
            str(item.req.specifier) for item in group
            if item.req is not None and str(item.req.specifier)
        ))
        if _specifier_set_empty(spec):
            conflicts.append(Conflict(
                package=package,
                requirements=[item.raw for item in group],
                reason=f"包 {package} 的版本约束没有交集",
                nodes=sorted({item.node for item in group}),
            ))
    return conflicts


def _specifier_set_empty(spec: SpecifierSet) -> bool:
    """保守识别确定空集；无法确定时返回 False，交给真实 resolver。"""
    specifiers = list(spec)
    if not specifiers:
        return False

    exact = [
        item.version for item in specifiers
        if item.operator in ("==", "===")
        and not item.version.endswith(".*")
    ]
    excluded = {
        item.version for item in specifiers
        if item.operator == "!=" and not item.version.endswith(".*")
    }
    if exact:
        distinct = set(exact)
        if len(distinct) > 1:
            first = next(iter(distinct))
            if any(not _same_version(first, value) for value in distinct):
                return True
        value = exact[0]
        if _contains_string(spec, value):
            return False
        return not _contains_string(spec, value)

    lower: tuple[Version, bool] | None = None
    upper: tuple[Version, bool] | None = None
    for item in specifiers:
        bounds = _bounds_for(item)
        if bounds is None:
            continue
        for kind, version, inclusive in bounds:
            if kind == "lower":
                lower = _pick_lower(lower, version, inclusive)
            else:
                upper = _pick_upper(upper, version, inclusive)

    if lower is None or upper is None:
        return False
    lower_version, lower_inclusive = lower
    upper_version, upper_inclusive = upper
    if lower_version > upper_version:
        return True
    if lower_version != upper_version:
        return False
    if not (lower_inclusive and upper_inclusive):
        return True
    only_version = str(lower_version)
    return any(_same_version(only_version, value) for value in excluded)


def _same_version(left: str, right: str) -> bool:
    """按 PEP 440 比较版本；无法解析的任意等值版本按原始字符串比较。"""
    left_version = _version(left)
    right_version = _version(right)
    if left_version is not None and right_version is not None:
        return left_version == right_version
    return left == right


def _contains_string(spec: SpecifierSet, value: str) -> bool:
    """判断 SpecifierSet 是否接受一个原始版本字符串；异常时交给 resolver。"""
    try:
        return spec.contains(Version(value), prereleases=True)
    except InvalidVersion:
        return any(
            item.operator in ("==", "===") and item.version == value
            for item in spec
        )


def _bounds_for(item: Specifier) -> list[tuple[str, Version, bool]] | None:
    """把可确定交集的 specifier 转成上下界；其余返回 None 交给 resolver。"""
    op = item.operator
    if op in (">=", ">"):
        version = _version(item.version)
        return None if version is None else [("lower", version, op == ">=")]
    if op in ("<=", "<"):
        version = _version(item.version)
        return None if version is None else [("upper", version, op == "<=")]
    if op == "~=":
        lower = _version(item.version)
        upper = _compatible_upper(item.version)
        if lower is None or upper is None:
            return None
        return [("lower", lower, True), ("upper", upper, False)]
    if op == "==" and item.version.endswith(".*"):
        prefix = item.version[:-2]
        lower = _version(prefix)
        upper = _wildcard_upper(prefix)
        if lower is None or upper is None:
            return None
        return [("lower", lower, True), ("upper", upper, False)]
    return None


def _version(raw: str) -> Version | None:
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _compatible_upper(raw: str) -> Version | None:
    version = _version(raw)
    if version is None:
        return None
    release = list(version.release)
    if len(release) == 1:
        release = [release[0] + 1]
    else:
        release = [*release[:-2], release[-2] + 1]
    return _version(".".join(str(part) for part in release))


def _wildcard_upper(prefix: str) -> Version | None:
    version = _version(prefix)
    if version is None:
        return None
    release = list(version.release)
    release[-1] += 1
    return _version(".".join(str(part) for part in release))


def _pick_lower(
    current: tuple[Version, bool] | None, version: Version, inclusive: bool
) -> tuple[Version, bool]:
    if current is None or version > current[0]:
        return version, inclusive
    if version == current[0] and not inclusive:
        return version, False
    return current


def _pick_upper(
    current: tuple[Version, bool] | None, version: Version, inclusive: bool
) -> tuple[Version, bool]:
    if current is None or version < current[0]:
        return version, inclusive
    if version == current[0] and not inclusive:
        return version, False
    return current


def _merge_requirements(items: list[_Item]) -> list[str]:
    """按包归并约束，保持首次出现顺序；URL、选项等 opaque 行原样保留。"""
    grouped: dict[str, list[_Item]] = {}
    for item in items:
        if item.req is not None:
            grouped.setdefault(canonicalize_name(item.req.name), []).append(item)

    merged: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.req is None:
            merged.append(item.raw)
            continue
        key = canonicalize_name(item.req.name)
        if key in seen:
            continue
        seen.add(key)
        merged.extend(_merge_group(grouped[key]))
    return merged


def _merge_group(group: list[_Item]) -> list[str]:
    requirements = [item.req for item in group if item.req is not None]
    if any(req.url for req in requirements):
        return [_render_requirement(req) for req in requirements]
    first = requirements[0]
    spec = SpecifierSet(",".join(str(req.specifier) for req in requirements if str(req.specifier)))
    extras = sorted({extra for req in requirements for extra in req.extras})
    name = first.name + (f"[{','.join(extras)}]" if extras else "")
    return [f"{name}{spec}" if str(spec) else name]


def _render_requirement(req: Requirement) -> str:
    extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
    url = f" @ {req.url}" if req.url else ""
    spec = str(req.specifier)
    return f"{req.name}{extras}{url}{spec}"


def _pip_resolve(
    requirements: list[str], *, force: bool = False
) -> tuple[str, str | None, list[str], bool]:
    """调用当前解释器的 pip 做真实解析；返回 (status, detail, log_tail, cached)。"""
    if not requirements:
        return "ok", None, [], False
    key = tuple(requirements)
    now = time.monotonic()
    if not force:
        with _PIP_CACHE_LOCK:
            cached = _PIP_CACHE.get(key)
        if cached is not None and cached[0] > now:
            status, detail, log = cached[1]
            return status, detail, list(log), True

    with tempfile.TemporaryDirectory(prefix="cb-pip-resolve-") as td:
        req_file = Path(td) / "requirements.txt"
        report = Path(td) / "report.json"
        req_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
                    "--no-cache-dir", "--no-input", "--report", str(report), "-r", str(req_file),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_RESOLVE_TIMEOUT,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "unavailable", f"pip 解析超时（>{_RESOLVE_TIMEOUT:g}s）", [], False
        except OSError as exc:
            return "unavailable", f"无法启动 pip：{exc}", [], False
        log = [
            line[:220] for line in (proc.stderr or proc.stdout or "").splitlines()
            if line.strip()
        ][-12:]
        if proc.returncode == 0:
            result: tuple[str, str | None, list[str]] = ("ok", None, log)
        else:
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if _looks_like_real_conflict(text):
                result = ("blocked", log[-1] if log else "pip 解析失败", log)
            else:
                result = (
                    "unavailable",
                    "pip 解析未完成（本机平台/Python 环境可能不匹配目标镜像）",
                    log,
                )

    with _PIP_CACHE_LOCK:
        _PIP_CACHE[key] = (now + _CACHE_TTL_SECONDS, result)
    return result[0], result[1], list(result[2]), False


def _looks_like_real_conflict(text: str) -> bool:
    """只认 pip resolver 的确定性冲突标志，避免 wheel 平台差异误报。"""
    lowered = text.lower()
    markers = (
        "resolutionimpossible",
        "conflicting dependencies",
        "the conflict is caused by",
    )
    return any(marker in lowered for marker in markers)


def precheck(nodes: list[dict], *, use_pip: bool = True, force: bool = False) -> dict:
    """对锁文件节点依赖做预检；返回 JSON 友好结构。"""
    items, syntax = _collect(nodes)
    conflicts = [*syntax, *_direct_conflicts(items)]
    node_names = sorted({item.node for item in items} | {n for c in conflicts for n in c.nodes})
    merged = _merge_requirements(items)
    if conflicts:
        return {
            "status": "blocked",
            "conflicts": [conflict.to_dict() for conflict in conflicts],
            "nodes": node_names,
            "merged_requirements": merged,
            "detail": "依赖约束存在直接冲突，未进入镜像重建",
            "log_tail": [],
            "cached": False,
        }
    if not use_pip:
        return {
            "status": "ok",
            "conflicts": [],
            "nodes": node_names,
            "merged_requirements": merged,
            "detail": "静态约束检查通过（未启用 pip resolve）",
            "log_tail": [],
            "cached": False,
        }
    status, detail, log, cached = _pip_resolve(merged, force=force)
    pip_conflicts: list[dict] = []
    if status == "blocked":
        pip_conflicts = [Conflict(
            package="pip resolver",
            requirements=merged,
            reason=detail or "pip resolver 确认依赖冲突",
            nodes=node_names,
        ).to_dict()]
    return {
        "status": status,
        "conflicts": pip_conflicts,
        "nodes": node_names,
        "merged_requirements": merged,
        "detail": detail,
        "log_tail": log,
        "cached": cached,
    }


def blocked_message(result: dict) -> str:
    conflicts = result.get("conflicts") or []
    if not conflicts:
        return str(result.get("detail") or "依赖预检失败")
    lines = ["节点依赖预检未通过，已取消镜像重建："]
    for item in conflicts[:5]:
        nodes = "、".join(item.get("nodes") or []) or "未知节点"
        requirements = "；".join(item.get("requirements") or [])
        detail = item.get("reason") or "约束冲突"
        lines.append(f"- {nodes}：{detail}")
        if requirements:
            lines.append(f"  约束：{requirements}")
    if len(conflicts) > 5:
        lines.append(f"- 另有 {len(conflicts) - 5} 项冲突，详见预检结果")
    return "\n".join(lines)

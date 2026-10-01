"""modal CLI 子进程封装（桥接器不直连 Modal API）。

凭证优先级：设置页保存的 token_id/token_secret（注入 MODAL_TOKEN_ID/MODAL_TOKEN_SECRET
环境变量）> CLI 自读的 ~/.modal.toml。只填了管理台凭证的用户也能直接用。

CLI 输出契约（modal 1.5.x 实测）：
- volume ls <vol> <path> --json
    → [{"filename": 含查询前缀的全路径, "type": "file"|"dir",
        "size": 人类可读串（如 "6 B" / "1.2 GB"）, "created_modified": 本地时间串}]
    不支持递归，深目录需逐层展开。
- volume put <vol> <local> [remote]   单文件/单目录上传；自动创建父目录；-f 覆盖
- volume get <vol> <remote> [dest]    下载；remote 为目录时递归
- volume rm <vol> <remote> [-r]       删除
- volume list --json                  所有卷 [{name, created_at, created_by}]（无大小）
- volume create <name>                新建卷
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess

from ..core.config import get_settings

_EXE: str | None = None

_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?i?B)", re.IGNORECASE)

# CLI quirk：`volume ls --json` 的 filename 对非 ASCII 双重转义（\u98ce 以字面 backslash+u 落地），
# json.loads 一次后仍是 "\\u98ce" 字面串；真实中文与 ASCII 名不受影响，仅名字含该模式时还原。
_DOUBLE_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape_cli_name(text: str) -> str:
    if "\\u" not in text:
        return text
    try:
        out = _DOUBLE_ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), text)
        out.encode("utf-8")  # 代理对拆出的孤立代理项在此失败 → 保留原样
        return out
    except (ValueError, OverflowError, UnicodeEncodeError):
        return text
_SIZE_UNITS = {
    "B": 1,
    "KB": 1024, "KIB": 1024,
    "MB": 1024**2, "MIB": 1024**2,
    "GB": 1024**3, "GIB": 1024**3,
    "TB": 1024**4, "TIB": 1024**4,
}


def parse_size(text: str) -> int:
    """把 CLI 的人类可读大小（"6 B" / "1.3 KiB" / "1.2 GB"）解析为字节数。"""
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).upper()])
    except (ValueError, KeyError):
        return 0


def _exe() -> str:
    global _EXE
    if _EXE is None:
        _EXE = shutil.which("modal") or "modal"
    return _EXE


class ModalCliError(RuntimeError):
    """modal CLI 非零退出 / 未安装。"""


def auth_env() -> dict[str, str]:
    """设置页保存的 Modal 凭证 → CLI 环境变量；未配置时返回空（回落 ~/.modal.toml）。

    strip 兜底：历史存量里可能已有带尾部空白的凭证（粘贴带入），
    直接注入会让 CLI 报 Token validation failed。
    """
    modal = get_settings().modal
    token_id, token_secret = modal.token_id.strip(), modal.token_secret.strip()
    if token_id and token_secret:
        return {"MODAL_TOKEN_ID": token_id, "MODAL_TOKEN_SECRET": token_secret}
    return {}


def volume_name() -> str:
    return get_settings().modal.volume


def _popen(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    try:
        # PYTHONWARNINGS=ignore：modal CLI 启动时向 stderr 打 DeprecationWarning，
        # 污染所有错误消息（破坏 "No such file or directory" 等匹配）
        return subprocess.Popen(
            [_exe(), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONWARNINGS": "ignore", **auth_env(), **(extra_env or {})},
        )
    except FileNotFoundError as exc:
        raise ModalCliError("找不到 modal CLI（请安装：pip install modal，并确认在 PATH 中）") from exc


async def run(*args: str, timeout: float = 600.0) -> str:
    """执行 CLI 命令并返回 stdout；阻塞部分放到线程池，避免占用事件循环。

    timeout 兜底防止 CLI 网络僵死把整条决策链路（/health、/prompt 路由）挂死。
    """
    proc = _popen(*args)
    try:
        out, err = await asyncio.wait_for(asyncio.to_thread(proc.communicate), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ModalCliError(f"modal {args[0]} 超时（超过 {timeout:g}s），已终止") from None
    if proc.returncode != 0:
        msg = (err or out).decode("utf-8", errors="replace").strip()
        msg = "\n".join(  # 兜底：剥掉 CLI 的 DeprecationWarning 噪音行
            line for line in msg.splitlines()
            if "DeprecationWarning" not in line and "set_event_loop_policy" not in line
        ).strip()
        raise ModalCliError(msg[:400] or f"modal {args[0]} 退出码 {proc.returncode}")
    return out.decode("utf-8", errors="replace")


def cli_json_list(text: str) -> list:
    """从 CLI stdout 提取 JSON 数组：输出可能混有 banner/日志行，定位首个 '[' 截取；无则空列表。

    JSON 本身不合法时照常抛 JSONDecodeError，由调用方的既有异常路径处理。
    """
    start = text.find("[")
    return json.loads(text[start:]) if start != -1 else []


async def list_dir(remote_path: str, volume: str | None = None) -> list[dict]:
    """列一层目录；filename 归一化为相对 remote_path 的名称。

    CLI quirk：`volume ls <vol> /` 会报 No such file or directory，省略路径参数才是列根。
    """
    args = ["volume", "ls", volume or volume_name()]
    prefix = ""
    if remote_path and remote_path not in ("/", "."):
        args.append(remote_path)
        prefix = remote_path.rstrip("/") + "/"
    out = await run(*args, "--json")
    items = cli_json_list(out)
    entries: list[dict] = []
    for item in items:
        full = str(item.get("filename", ""))
        rel = full[len(prefix):].strip("/") if prefix and full.startswith(prefix) else full.rsplit("/", 1)[-1]
        rel = rel.strip("/")
        if not rel:
            continue
        entries.append({
            "name": _unescape_cli_name(rel),
            "type": item.get("type", "file"),
            "size": str(item.get("size", "")),
            "mtime": str(item.get("created_modified", "")),
        })
    return entries


async def list_files(remote_dir: str, max_depth: int = 3) -> list[dict]:
    """递归收集目录下所有文件，name 为相对 remote_dir 的 POSIX 路径。"""
    files: list[dict] = []

    async def walk(rel_dir: str, depth: int) -> None:
        path = f"{remote_dir}/{rel_dir}" if rel_dir else remote_dir
        for entry in await list_dir(path):
            rel = f"{rel_dir}/{entry['name']}" if rel_dir else entry["name"]
            if entry["type"] in ("dir", "directory"):
                if depth < max_depth:
                    await walk(rel, depth + 1)
            else:
                files.append({**entry, "name": rel})

    await walk("", 0)
    return files

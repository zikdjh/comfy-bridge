"""设置键级推拉 API（M2-5）：两端 comfy.settings.json 的键级 diff 与按方向合并执行。

- 本地 = `<comfy_root>/user/default/comfy.settings.json`（ComfyUI 用户设置，保存后热生效）
- 云端 = Modal 数据卷 `user/default/comfy.settings.json`（runner 以 --base-directory /data
  挂载、启动时读取；容器运行中改卷不热生效，下次冷启动生效 —— 由 UI 提示）

读经 SDK 网关 get_file、写经 put_file（复用 modal_volume.client 的凭证注入与超时兜底），
不要求云端容器在运行。凭证类键（键名含 api_key/token/secret/password）
在 diff 回显中一律掩码（红线），推拉仍使用真实值。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.config import get_settings
from ...modal_volume import client as volume_cli
from ...modal_volume.client import ModalCliError
from ...sync import settings_sync as ss

router = APIRouter(prefix="/settings-sync", tags=["settings-sync"])

CLOUD_REL = "user/default/comfy.settings.json"

_MISSING_HINTS = ("no such file", "not found", "does not exist")


def _local_path() -> Path:
    return Path(get_settings().local.comfy_root) / "user" / "default" / "comfy.settings.json"


def _read_json_file(path: Path) -> dict:
    """读 JSON 对象；缺失/解析失败/非对象一律视为空 dict（由上层 available 标注说明）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _read_cloud() -> tuple[dict | None, bool, str | None]:
    """经 SDK 网关读云端设置。返回 (settings, file_exists, error)。

    error 非 None 表示云端通道不可用（SDK 不可用/凭证/网络）；文件缺失不算错误，
    file_exists=False 且 settings={}（首次部署尚未生成，视为空）。
    """
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "comfy.settings.json"
        try:
            await volume_cli.get_file(CLOUD_REL, dest, force=True)
        except ModalCliError as exc:
            msg = str(exc)
            if any(hint in msg.lower() for hint in _MISSING_HINTS):
                return {}, False, None
            return None, False, msg[:300]
        data = await asyncio.to_thread(_read_json_file, dest)
    return data, True, None


async def _write_cloud(data: dict) -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "comfy.settings.json"
        src.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        await volume_cli.put_file(src, CLOUD_REL, force=True)


async def _write_local(data: dict) -> None:
    path = _local_path()

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        tmp.replace(path)  # 原子替换，避免 ComfyUI 恰在写盘时读到半截

    await asyncio.to_thread(_write)


def _display_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "key": r["key"],
            "local": ss.mask_secret(r["key"], r["local"]) if r["local"] is not None else None,
            "cloud": ss.mask_secret(r["key"], r["cloud"]) if r["cloud"] is not None else None,
            "status": r["status"],
            "sensitive": r["sensitive"],
            "default_direction": r["default_direction"],
        }
        for r in rows
    ]


@router.get("")
async def settings_diff() -> dict:
    """两端设置键级 diff（值已按凭证红线掩码，仅用于展示；执行时后端重读真实值）。"""
    local_path = _local_path()
    local = _read_json_file(local_path)
    cloud, cloud_exists, cloud_err = await _read_cloud()
    if cloud is None:  # 通道不可用：仍返回本地清单，rows 置空由前端提示
        rows: list[dict] = []
    else:
        rows = _display_rows(ss.diff_settings(local, cloud))
    return {
        "volume": volume_cli.volume_name(),
        "cloud_path": CLOUD_REL,
        "local_path": str(local_path),
        "local_exists": local_path.exists(),
        "cloud_available": cloud is not None,
        "cloud_exists": cloud_exists,
        "cloud_error": cloud_err,
        "rows": rows,
    }


class ApplyBody(BaseModel):
    # {设置键: push(本地→云端) | pull(云端→本地)}；未提到的键保持原值
    directions: dict[str, Literal["push", "pull"]]


@router.post("/apply")
async def settings_apply(body: ApplyBody) -> dict:
    """按方向执行键级推拉：后端重读两端真实值 → 合并 → 分别落盘/落卷。"""
    if not body.directions:
        raise HTTPException(status_code=422, detail="directions 为空：至少指定一个设置键的方向")
    local = _read_json_file(_local_path())
    cloud, _exists, cloud_err = await _read_cloud()
    if cloud is None:
        raise HTTPException(status_code=503, detail=f"云端设置不可读: {cloud_err}")

    new_local, new_cloud, pushed, pulled, skipped = ss.apply_directions(local, cloud, body.directions)
    if pulled:
        await _write_local(new_local)
    if pushed:
        try:
            await _write_cloud(new_cloud)
        except ModalCliError as exc:
            raise HTTPException(status_code=502, detail=f"写入云端数据卷失败: {exc}") from exc
    return {
        "pushed": pushed,
        "pulled": pulled,
        "skipped": skipped,
        "local_written": bool(pulled),
        "cloud_written": bool(pushed),
    }

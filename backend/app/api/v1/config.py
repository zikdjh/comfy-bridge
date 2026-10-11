"""桥接器配置 API（规划文档 §5 GET/PUT /api/config）。

读取返回「默认值 + config.toml 覆盖」的合并视图（modal.token_secret 脱敏）；
PUT 校验后把 local/bridge/modal 三节整体写回 ~/.comfybridge/config.toml 并清除
设置缓存（进程内热生效，无需重启）。token_secret 的 PUT 语义：留空=保留现有，
显式 null=清除。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ...core import comfy_scan
from ...core.config import CONFIG_PATH, AppConfig, BridgeConfig, ModalConfig, get_settings
from ...proxy import object_info as object_info_merge

router = APIRouter(prefix="/config", tags=["config"])

_HEADER = "# ComfyBridge 配置（管理台「设置」页写入；backend/config.example.toml 为字段说明）\n"


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:5]}****{secret[-4:]}" if len(secret) >= 12 else "****"


def _modal_view(modal: ModalConfig) -> dict:
    # token_secret / proxy_token_secret 永不回显；volume/task_timeout 等非敏感执行参数正常回显供设置页编辑
    return {
        "token_id": modal.token_id,
        "environment": modal.environment,
        "has_secret": bool(modal.token_secret),
        "secret_masked": _mask_secret(modal.token_secret),
        "proxy_token_id": modal.proxy_token_id,
        "has_proxy_secret": bool(modal.proxy_token_secret),
        "proxy_secret_masked": _mask_secret(modal.proxy_token_secret),
        "volume": modal.volume,
        "task_timeout": modal.task_timeout,
        # Runner 通道参数（非敏感，health 也回显 web_url）：配置向导步骤读取/写入
        "runner_enabled": modal.runner_enabled,
        "web_url": modal.web_url,
    }


def _view() -> dict:
    settings = get_settings()
    return {
        "config_path": str(CONFIG_PATH),
        "local": settings.local.model_dump(),
        "bridge": settings.bridge.model_dump(),
        "modal": _modal_view(settings.modal),
    }


def _dump_toml(sections: dict[str, dict]) -> str:
    def toml_str(v) -> str:
        # TOML basic string：反斜杠必须转义（Windows 路径含 \），引号同理
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def render(value) -> str:
        """递归渲染值：dict → 内联表（list 里的 dict 也必须走这里，否则退化成 Python repr 字符串）。"""
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            inner = ", ".join(f"{k} = {render(v)}" for k, v in value.items())
            return f"{{ {inner} }}"
        if isinstance(value, (list, tuple)):  # 如 extra_launch_args / launch_command / rules
            return "[" + ", ".join(render(v) for v in value) + "]"
        return toml_str(value)

    lines = [_HEADER.rstrip("\n")]
    for section, values in sections.items():
        lines.append(f"\n[{section}]")
        lines.extend(f"{k} = {render(v)}" for k, v in values.items() if v is not None)
    return "\n".join(lines) + "\n"


class ConfigBody(BaseModel):
    local: dict | None = None
    bridge: dict | None = None
    modal: dict | None = None


def _merge_modal(current: dict, incoming: dict) -> dict:
    """token_secret / proxy_token_secret：留空（或缺省）= 保留现有；显式 null = 清除。

    其余键缺省 = 保留现有；null 一律忽略（此前 null 会清空任意字段，
    PUT {"volume": null} 即可瘫痪卷操作——现只对两个 secret 保留清除语义）。
    字符串值一律 strip：粘贴凭证时带入的尾部空白会让 modal CLI 报
    "Token validation failed"，且极难排查。
    """
    merged = dict(current)
    secret_keys = ("token_secret", "proxy_token_secret")
    for key, value in incoming.items():
        if isinstance(value, str):
            value = value.strip()
        if key in secret_keys:
            if value is None:
                merged[key] = ""
            elif value == "":
                continue  # 留空 = 不修改
            else:
                merged[key] = value
        elif value is None:
            continue
        else:
            merged[key] = value
    return merged


@router.get("")
async def read_config() -> dict:
    return _view()


@router.put("")
async def write_config(body: ConfigBody) -> dict:
    current = get_settings()
    incoming_local = body.local or {}
    # comfy_root 变更时强制完整性校验（与「校验」按钮同一判据），不合格直接拒绝
    if "comfy_root" in incoming_local:
        new_root = str(incoming_local.get("comfy_root") or "").strip().strip('"')
        if not new_root:
            raise HTTPException(status_code=422, detail="ComfyUI 根目录不能为空")
        if not Path(new_root).is_absolute():
            raise HTTPException(status_code=422, detail=f"ComfyUI 根目录必须是绝对路径: {new_root!r}")
        if new_root != current.local.comfy_root:
            report = comfy_scan.inspect_root(new_root)
            if not report["valid"]:
                missing = "、".join(report["required_missing"])
                detail = "目录不存在" if not report["exists"] else f"缺少 {missing}"
                raise HTTPException(
                    status_code=422,
                    detail=f"ComfyUI 根目录校验失败：{new_root}（{detail}）",
                )
    clean_local = {**incoming_local}
    if "comfy_root" in clean_local:
        clean_local["comfy_root"] = new_root
    merged = {
        "local": {**current.local.model_dump(), **clean_local},
        "bridge": {**current.bridge.model_dump(), **(body.bridge or {})},
        "modal": _merge_modal(current.modal.model_dump(), body.modal or {}),
    }
    try:
        clean = AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"配置校验失败: {exc.errors()[:3]}")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        _dump_toml({
            "local": clean.local.model_dump(),
            "bridge": clean.bridge.model_dump(),
            "modal": clean.modal.model_dump(),
        }),
        encoding="utf-8",
    )
    get_settings.cache_clear()
    object_info_merge.invalidate_merge_cache()
    return _view()


def update_bridge_section(updates: dict) -> dict:
    """bridge 节增量更新并写回 config.toml（cost API 预算护栏设置复用同一落盘路径）。

    返回合并后的配置视图；校验失败抛 422。进程内热生效（cache_clear）。
    """
    current = get_settings()
    merged_bridge = {**current.bridge.model_dump(), **updates}
    try:
        bridge = BridgeConfig(**merged_bridge)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"配置校验失败: {exc.errors()[:3]}")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        _dump_toml({
            "local": current.local.model_dump(),
            "bridge": bridge.model_dump(),
            "modal": current.modal.model_dump(),
        }),
        encoding="utf-8",
    )
    get_settings.cache_clear()
    object_info_merge.invalidate_merge_cache()
    return _view()


# ---------------------------------------------------------------- 本机 ComfyUI 扫描
class ValidateComfyBody(BaseModel):
    path: str


@router.post("/validate-comfy")
async def validate_comfy_root(body: ValidateComfyBody) -> dict:
    """手动指定目录的完整性校验：必需项缺失 = 不可用（不落任何配置）。"""
    raw = (body.path or "").strip().strip('"')
    if not raw:
        raise HTTPException(status_code=422, detail="请填写目录路径")
    if not Path(raw).is_absolute():
        raise HTTPException(status_code=422, detail=f"请填写绝对路径: {raw!r}")
    return comfy_scan.inspect_root(raw)


@router.get("/scan-comfy")
async def read_comfy_scan() -> dict:
    """当前扫描状态（前端 1.5s 轮询；idle/done 结果保留供回看）。"""
    return comfy_scan.state()


@router.post("/scan-comfy")
async def start_comfy_scan() -> dict:
    """启动一次本机扫描（固定/可移动磁盘，排除 C 盘）。"""
    try:
        comfy_scan.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return comfy_scan.state()


@router.post("/scan-comfy/cancel")
async def cancel_comfy_scan() -> dict:
    comfy_scan.cancel()
    return comfy_scan.state()

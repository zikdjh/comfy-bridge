"""桥接器配置 API（规划文档 §5 GET/PUT /api/config）。

读取返回「默认值 + config.toml 覆盖」的合并视图（modal.token_secret 脱敏）；
PUT 校验后把 local/bridge/modal 三节整体写回 ~/.comfybridge/config.toml 并清除
设置缓存（进程内热生效，无需重启）。token_secret 的 PUT 语义：留空=保留现有，
显式 null=清除。
"""

from __future__ import annotations

import asyncio
import glob
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ...core import audit, comfy_scan, pricing, update_check
from ...core.config import (
    CONFIG_PATH,
    AccountProfile,
    AppConfig,
    BridgeConfig,
    ModalConfig,
    get_settings,
    modal_configured,
)
from ...logs.store import count_active_modal
from ...modal_volume import client as modal_cli
from ...nodes_mgr import deploy, diff_cache
from ...proxy import object_info as object_info_merge
from ...proxy import passthrough
from ...runner import client as runner_client
from ...volume_mgr import usage as volume_usage

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
        "app_name": modal.app_name,
        "extra_launch_args": list(modal.extra_launch_args),
        # 云端资源规格（部署期生效：保存后需「应用并重新部署」才作用于容器）
        "gpu": modal.gpu,
        "cpu": modal.cpu,
        "memory_mb": modal.memory_mb,
    }


def _view() -> dict:
    settings = get_settings()
    return {
        "config_path": str(CONFIG_PATH),
        "local": settings.local.model_dump(),
        "bridge": settings.bridge.model_dump(),
        "modal": _modal_view(settings.modal),
    }


# ---------------------------------------------------------------- 账号档案（多账号维护）

def _accounts_active_name(modal: ModalConfig) -> str | None:
    """活跃档案判定：优先使用显式标记，兼容旧配置再按 token_id 推断。

    “保存当前为档案”允许同凭据生成新名称；仅按 token_id 判断会让所有
    同凭据档案都被视为激活，无法切换或删除。
    """
    if modal.active_account:
        return next(
            (p.name for p in modal.accounts if p.name == modal.active_account),
            None,
        )
    if not modal.token_id:
        return None
    return next((p.name for p in modal.accounts if p.token_id == modal.token_id), None)


def _account_view(profile: AccountProfile, active_name: str | None) -> dict:
    return {
        "name": profile.name,
        "token_id": profile.token_id,
        "has_secret": bool(profile.token_secret),
        "secret_masked": _mask_secret(profile.token_secret),
        "environment": profile.environment,
        "proxy_token_id": profile.proxy_token_id,
        "has_proxy_secret": bool(profile.proxy_token_secret),
        "proxy_secret_masked": _mask_secret(profile.proxy_token_secret),
        "volume": profile.volume,
        "app_name": profile.app_name,
        "web_url": profile.web_url,
        "task_timeout": profile.task_timeout,
        "extra_launch_args": list(profile.extra_launch_args),
        "active": profile.name == active_name,
    }


def _accounts_payload(modal: ModalConfig) -> dict:
    active = _accounts_active_name(modal)
    return {"accounts": [_account_view(p, active) for p in modal.accounts], "active": active}


def _persist_settings(local, bridge, modal) -> None:
    """整体写回 config.toml 并刷新进程内配置缓存。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        _dump_toml({
            "local": local.model_dump(),
            "bridge": bridge.model_dump(),
            "modal": modal.model_dump(),
        }),
        encoding="utf-8",
    )
    get_settings.cache_clear()


def _invalidate_account_caches() -> None:
    """账号切换联动（docs/ComfyBridge-设计-多账号维护.html §3.3/§3.4）：配置缓存由 _persist_settings 清。"""
    object_info_merge.invalidate_merge_cache()
    runner_client.invalidate_state()
    volume_usage.invalidate_cache()
    modal_cli.reset_runtime_state()
    diff_cache.invalidate_all()


class AccountUpsertBody(BaseModel):
    name: str
    token_id: str = ""
    token_secret: str = ""       # 已存在档案留空 = 保留；新档案语义见 upsert_account
    environment: str = ""
    proxy_token_id: str = ""
    proxy_token_secret: str = ""  # 同上
    volume: str = "comfyui-data"
    app_name: str = "comfyui-l40s"
    web_url: str = ""
    task_timeout: int = 3600
    extra_launch_args: list[str] = []


class AccountActivateBody(BaseModel):
    force: bool = False


@router.get("/accounts")
async def list_accounts() -> dict:
    return _accounts_payload(get_settings().modal)


@router.put("/accounts")
async def upsert_account(body: AccountUpsertBody) -> dict:
    """新增或覆盖档案；同名 = 覆盖（轮换/更新语义），token_id 跨档案唯一（1 账号 = 1 份凭据）。"""
    name = body.name.strip()
    token_id = body.token_id.strip()
    if not name:
        raise HTTPException(status_code=422, detail="档案名称不能为空")
    if not token_id:
        raise HTTPException(status_code=422, detail="Token ID 不能为空")
    settings = get_settings()
    modal = settings.modal
    existing = next((p for p in modal.accounts if p.name == name), None)
    if any(p.token_id == token_id for p in modal.accounts if p.name != name):
        raise HTTPException(status_code=409, detail=f"该 Token ID 已登记为其它账号档案（1 账号 = 1 份凭据）：{token_id}")
    if existing is not None:
        # 已存在档案：secret 留空 = 保留原值（覆盖更新不要求重复粘贴密钥）
        token_secret = body.token_secret or existing.token_secret
        proxy_secret = body.proxy_token_secret or existing.proxy_token_secret
    elif token_id == modal.token_id:
        # 新档案但 token 与当前活跃一致（「保存当前为档案」）：留空 = 沿用当前密钥
        token_secret = body.token_secret or modal.token_secret
        proxy_secret = body.proxy_token_secret or modal.proxy_token_secret
    else:
        if not body.token_secret:
            raise HTTPException(status_code=422, detail="新档案必须填写 Token Secret（与当前 token 相同时可留空沿用）")
        token_secret = body.token_secret
        proxy_secret = body.proxy_token_secret
    profile = AccountProfile(
        name=name,
        token_id=token_id,
        token_secret=token_secret,
        environment=body.environment.strip(),
        proxy_token_id=body.proxy_token_id.strip(),
        proxy_token_secret=proxy_secret,
        volume=body.volume.strip() or "comfyui-data",
        app_name=body.app_name.strip() or "comfyui-l40s",
        web_url=body.web_url.strip(),
        task_timeout=body.task_timeout,
        extra_launch_args=list(body.extra_launch_args),
    )
    accounts = [*(p for p in modal.accounts if p.name != name), profile]
    if modal.active_account and any(p.name == modal.active_account for p in accounts if p.name != name):
        active_account = modal.active_account
    elif token_id == modal.token_id and not modal.active_account:
        active_account = name
    else:
        active_account = modal.active_account
    new_modal = modal.model_copy(update={
        "accounts": accounts,
        "active_account": active_account,
    })
    _persist_settings(settings.local, settings.bridge, new_modal)
    audit.record({"event": "account_profile_save", "name": name})
    return _accounts_payload(new_modal)


@router.delete("/accounts/{name}")
async def delete_account(name: str) -> dict:
    settings = get_settings()
    modal = settings.modal
    if not any(p.name == name for p in modal.accounts):
        raise HTTPException(status_code=404, detail=f"账号档案不存在：{name}")
    if _accounts_active_name(modal) == name:
        raise HTTPException(status_code=409, detail="不能删除当前激活的账号档案（请先激活其它账号）")
    new_modal = modal.model_copy(update={
        "accounts": [p for p in modal.accounts if p.name != name],
        "active_account": "" if modal.active_account == name else modal.active_account,
    })
    _persist_settings(settings.local, settings.bridge, new_modal)
    audit.record({"event": "account_profile_delete", "name": name})
    return _accounts_payload(new_modal)


@router.post("/accounts/{name}/activate")
async def activate_account(name: str, body: AccountActivateBody | None = None) -> dict:
    """激活档案：字段整体复制进 [modal] 生效视图 + 全量缓存失效（§4.3 流程）。"""
    settings = get_settings()
    modal = settings.modal
    profile = next((p for p in modal.accounts if p.name == name), None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"账号档案不存在：{name}")
    if _accounts_active_name(modal) == name:
        return _accounts_payload(modal)
    active_tasks = count_active_modal()
    if active_tasks > 0 and not (body and body.force):
        raise HTTPException(
            status_code=409,
            detail=f"存在 {active_tasks} 个在途云端任务，切换账号会导致结果无法采集；确认切换请携带 force=true",
        )
    new_modal = modal.model_copy(update={
        "active_account": profile.name,
        "token_id": profile.token_id,
        "token_secret": profile.token_secret,
        "environment": profile.environment,
        "proxy_token_id": profile.proxy_token_id,
        "proxy_token_secret": profile.proxy_token_secret,
        "volume": profile.volume,
        "app_name": profile.app_name,
        "web_url": profile.web_url,
        "task_timeout": profile.task_timeout,
        "extra_launch_args": list(profile.extra_launch_args),
    })
    _persist_settings(settings.local, settings.bridge, new_modal)
    _invalidate_account_caches()
    audit.record({"event": "account_activate", "name": name, "forced": bool(body and body.force)})
    return {**_accounts_payload(new_modal), "modal": _modal_view(new_modal)}


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


# ---------------------------------------------------------------- Node.js 环境检测

def _detect_node_candidates() -> list[Path]:
    """PATH 探测不到时的常见安装布局快扫（官方安装器 / nvm-windows / fnm / volta / scoop）。"""
    home = Path.home()
    patterns: list[Path] = [
        Path(r"C:\Program Files\nodejs") / "node.exe",
        Path(r"C:\Program Files (x86)\nodejs") / "node.exe",
        home / "scoop" / "apps" / "nodejs" / "current" / "node.exe",
        home / "AppData" / "Roaming" / "nvm" / "*" / "node.exe",
        home / "AppData" / "Local" / "Volta" / "tools" / "image" / "node" / "*" / "node.exe",
        home / "AppData" / "Local" / "fnm_multishells" / "*" / "node.exe",
        home / ".fnm" / "node-versions" / "*" / "installation" / "node.exe",
    ]
    out: list[Path] = []
    for pattern in patterns:
        try:
            if pattern.is_file():
                out.append(pattern)
                continue
            # 版本号目录取最新（倒序前 3 个）；fnm_multishells 命中即可
            for hit in sorted(glob.glob(str(pattern)), reverse=True)[:3]:
                p = Path(hit)
                if p.is_file():
                    out.append(p)
        except OSError:
            continue
    return out


def _node_version(node_exe: str) -> str | None:
    """执行 node --version；失败/超时返回 None（不抛，检测接口保持可轮询语义）。"""
    try:
        proc = subprocess.run(
            [node_exe, "--version"],
            capture_output=True, text=True, timeout=6,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or "").strip()
    return text if proc.returncode == 0 and text else None


@router.get("/node-env")
async def read_node_env() -> dict:
    """Node.js 环境检测（设置页「本地」Tab）：手动指定路径 → PATH → 常见安装目录快扫。

    检测是幂等只读（最多跑一次 node --version 子进程），dev.ps1/pnpm 前置条件可视化；
    手动指定路径在 GET /config 的 local.node_path 回显，保存走 PUT /config（轻校验）。
    """
    configured = (get_settings().local.node_path or "").strip().strip('"')
    if configured:
        p = Path(configured)
        if not p.is_file():
            return {"ok": False, "source": "config", "path": configured,
                    "error": f"手动指定路径不存在: {configured}"}
        version = await asyncio.to_thread(_node_version, str(p))
        if version:
            return {"ok": True, "source": "config", "path": str(p), "version": version}
        return {"ok": False, "source": "config", "path": configured,
                "error": f"手动指定路径无法执行 node --version: {configured}"}
    which = shutil.which("node")
    if which:
        version = await asyncio.to_thread(_node_version, which)
        if version:
            return {"ok": True, "source": "PATH", "path": which, "version": version}
    for candidate in await asyncio.to_thread(_detect_node_candidates):
        version = await asyncio.to_thread(_node_version, str(candidate))
        if version:
            return {"ok": True, "source": "scan", "path": str(candidate), "version": version}
    return {"ok": False, "source": "none", "path": None,
            "error": "PATH 与常见安装目录均未找到 node.exe；请在下方手动指定路径后保存"}


class ConfigBody(BaseModel):
    local: dict | None = None
    bridge: dict | None = None
    modal: dict | None = None


_GPU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,39}$")


def _validate_modal_resources(incoming: dict) -> None:
    """云端资源规格轻校验：只挡明显非法值。

    型号与配比是否被 Modal 接受，最终由 modal deploy 判定（失败会在部署任务里
    回显原始错误）；这里避免把空型号、负数核心数之类写进配置。
    """
    if incoming.get("gpu") is not None:
        gpu = str(incoming["gpu"]).strip()
        if not _GPU_RE.match(gpu):
            raise HTTPException(
                status_code=422,
                detail=f"GPU 型号格式非法: {gpu!r}（示例：L40S / A100-80GB / H100:2）",
            )
    if incoming.get("cpu") is not None:
        try:
            cpu = float(incoming["cpu"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="CPU 核心数必须是数字") from None
        if not 0.125 <= cpu <= 64:
            raise HTTPException(
                status_code=422, detail=f"CPU 核心数需在 0.125–64 之间（当前 {cpu:g}）"
            )
    if incoming.get("memory_mb") is not None:
        try:
            memory = int(incoming["memory_mb"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="内存必须是整数（单位 MiB）") from None
        if not 256 <= memory <= 262144:
            raise HTTPException(
                status_code=422, detail=f"内存需在 256 MiB–256 GiB 之间（当前 {memory} MiB）"
            )


def _merge_modal(current: dict, incoming: dict) -> dict:
    """token_secret / proxy_token_secret：留空（或缺省）= 保留现有；显式 null = 清除。

    其余键缺省 = 保留现有；null 一律忽略（此前 null 会清空任意字段，
    PUT {"volume": null} 即可瘫痪卷操作——现只对两个 secret 保留清除语义）。
    字符串值一律 strip：粘贴凭证时带入的尾部空白会让 Modal 认证报
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
    if "node_path" in clean_local:
        raw_node = str(clean_local.get("node_path") or "").strip().strip('"')
        if raw_node:
            np = Path(raw_node)
            if not np.is_absolute() or not np.is_file():
                raise HTTPException(
                    status_code=422,
                    detail=f"Node.js 路径必须是已存在的可执行文件绝对路径: {raw_node!r}（留空 = 自动检测）",
                )
        clean_local["node_path"] = raw_node
    _validate_modal_resources(body.modal or {})
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
    # 常驻透传客户端在 trust_env 变更后需重建（本轮配置含 use_system_proxy 时）；
    # close 是幂等的，其他配置写入路径多关一次无副作用。
    await passthrough.close_upstream_client()
    return _view()


@router.post("/modal/resources/apply")
async def apply_modal_resources() -> dict:
    """按当前配置重新部署 runner，使 gpu/cpu/memory 生效。

    Modal 的资源规格只能在 deploy 时固化（runner.py 从 CB_MODAL_* 环境变量读取），
    因此这里与「镜像重建」「版本对齐」共用 nodes_mgr.deploy 的单飞通道：同一时间
    只允许一个部署在跑，冲突返回 409。部署是后台任务，进度经 SSE `deploy` 事件
    推送，也可轮询 GET /config/modal/resources/status。
    """
    if not modal_configured():
        raise HTTPException(status_code=400, detail="未配置 Modal 凭证（设置 → Modal）")
    spec = get_settings().modal
    label = (
        f"云端资源规格 → {spec.gpu} / {spec.cpu:g} 核 / {spec.memory_mb / 1024:g} GiB"
    )
    try:
        job = await deploy.begin_exclusive(label)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    deploy.spawn(None)
    audit.record({"event": "modal_resources_apply", "gpu": spec.gpu, "cpu": spec.cpu,
                  "memory_mb": spec.memory_mb})
    return {"ok": job["state"] in ("running", "ok"), "job": job}


@router.get("/modal/resources/status")
async def modal_resources_status() -> dict:
    """资源规格部署任务状态（设置页轮询用；与镜像重建/版本对齐同一任务对象）。"""
    return {"job": deploy.job_state()}


@router.get("/modal/pricing")
async def modal_pricing(
    gpu: str | None = None,
    cpu: float | None = None,
    memory_mb: int | None = None,
) -> dict:
    """Modal 资源参考价与当前配置的每小时预估值。

    不传参数时按已保存配置估算；设置页编辑表单时带参数实时预览，无需先保存。
    价格是参考值，界面需同时展示 source/note，不能当作账单金额。
    """
    spec = get_settings().modal
    return pricing.estimate_hourly(
        gpu if gpu is not None else spec.gpu,
        cpu if cpu is not None else spec.cpu,
        memory_mb if memory_mb is not None else spec.memory_mb,
    ) | {"catalog": pricing.catalog()}


async def update_bridge_section(updates: dict) -> dict:
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
    await passthrough.close_upstream_client()
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


@router.get("/update-check")
async def get_update_check(force: bool = False) -> dict:
    """整合包更新检查：启动时自动调用（24h 节流）；设置页手动检查带 force=1 强制真查。

    未打包运行（无 version.txt）返回 not_packaged，前端据此不展示横幅。
    """
    return await update_check.check_update(force=force)


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

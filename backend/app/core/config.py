"""桥接器配置加载。

优先级：~/.comfybridge/config.toml > 内置默认值。
字段与 backend/config.example.toml 保持一致。
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

CONFIG_PATH = Path.home() / ".comfybridge" / "config.toml"


class LocalConfig(BaseModel):
    comfy_url: str = "http://127.0.0.1:8188"
    # 本地 ComfyUI 仓库根（含 main.py/nodes.py/comfy/ 等目录）。
    # 默认空 = 未配置：首次使用请在管理台「设置 → 本地」扫描/校验后填入（保存热生效），
    # 不内置开发机绝对路径（换机即坏）。
    comfy_root: str = ""
    # 远端执行结果回传落盘处（M1 使用）；默认空 = 未配置，设置页必填
    output_dir: str = ""
    # 本地模型目录映射（分类 → 绝对路径）；空项回退 comfy_root/models/<分类>
    models_dirs: dict[str, str] = {}
    # 本地 ComfyUI 追加启动参数（管理台「设置 → 启动命令」写入；
    # 经「设置 → 启动命令 → 重启」由桥接器重启本地进程后生效）
    extra_launch_args: list[str] = []
    # 桥接器接管启动用的基础命令（argv，可执行文件 + main.py + 基础参数）；
    # 空 = 保存/启动时自动探测当前实例命令行，探测不到回退 venv python -s main.py
    launch_command: list[str] = []


class RoutingRule(BaseModel):
    """自定义路由规则（bridge.rules，M3）。

    宽松模型：结构校验在 API 层（routing.engine.validate_rules），引擎对未知
    kind/目标一律跳过并留痕——config.toml 被手编坏值不至于毒化整个决策链。
    """

    id: str = ""
    name: str = ""
    kind: str = ""  # workflow_tag | gen_type | min_pixels（见 routing.engine.RULE_KINDS）
    params: dict[str, object] = {}
    target: str = ""  # local | modal
    enabled: bool = True


class BridgeConfig(BaseModel):
    listen: str = "127.0.0.1:8189"
    # 路由规则（§3.1）：default 规则兜底 / 视频类请求目标 / 大分辨率阈值（0=关闭）/ Modal 不可用策略
    default_backend: str = "local"
    video_backend: str = "modal"
    large_side_pixels: int = 0
    on_modal_unavailable: str = "fallback_local"  # fallback_local | reject
    # —— 成本估算与预算护栏（M4-1，规划文档 §3.6）——
    gpu_hourly_rate_usd: float = 1.95  # GPU 单价（$/容器·小时，随 Modal 价格在设置页更新）
    monthly_budget_usd: float = 0.0    # 月度预算上限（$），0 = 不启用护栏
    on_budget_exceed: str = "fallback_local"  # fallback_local | reject
    # 云端最大并发护栏（M4-1 收尾）：在途云端任务（final_backend=modal 且 queued/running）
    # 达到上限时，决策目标 modal 的请求按 on_modal_unavailable 策略回落本地或拒绝；0 = 不限制。
    # 计数源 logs/store.count_active_modal；保守护栏（计数与入队无事务，±1 竞态可容忍）。
    max_cloud_concurrent: int = 0
    # —— 自定义路由规则链（M3，规划文档 §3.1）——
    # 空列表 = 沿用内置 gen_type/size 规则（历史行为不变）；非空 = 自定义链按序求值并
    # 替换内置那两条；manual/tag/cloud_model/default 四步固定不参与 CRUD。
    rules: list[RoutingRule] = []


class ModalConfig(BaseModel):
    # Modal 平台认证（modal token new 生成，或 modal.com 控制台）。
    # token_secret 仅落盘本文件（用户目录，不入代码库）；API 层脱敏回显。
    token_id: str = ""      # ak-...
    token_secret: str = ""  # as-...
    environment: str = ""   # Modal 环境，空 = 默认环境
    # —— Runner 端点代理鉴权（web_server requires_proxy_auth=True）——
    # 代理鉴权 token 是独立凭证族（wk-…/ws-…，modal.com/settings/proxy-auth-tokens 创建），
    # 与账户 API token（ak-/as-）不通；仅落盘本文件，API 层脱敏回显
    proxy_token_id: str = ""      # wk-...
    proxy_token_secret: str = ""  # ws-...
    volume: str = "comfyui-data"  # 模型/节点持久化数据卷名
    # —— Modal Runner 执行通道（M1）——
    runner_enabled: bool = True
    app_name: str = "comfyui-l40s"  # modal-runner/runner.py 部署的应用名
    # web_server 固定端点（modal deploy 输出），空 = 通道不可用
    web_url: str = ""
    task_timeout: int = 3600  # 单个云端任务最长等待秒数（含冷启动）
    # 云端 ComfyUI 追加启动参数：保存时经 modal volume put 写入数据卷
    # /config/launch_args.json，容器冷启动时由 runner.py 读取追加
    extra_launch_args: list[str] = []


class AppConfig(BaseModel):
    local: LocalConfig = LocalConfig()
    bridge: BridgeConfig = BridgeConfig()
    modal: ModalConfig = ModalConfig()


def _load_toml() -> dict:
    if CONFIG_PATH.is_file():
        with CONFIG_PATH.open("rb") as f:
            return tomllib.load(f)
    return {}


@lru_cache
def get_settings() -> AppConfig:
    data = _load_toml()
    known = {k: v for k, v in data.items() if k in {"local", "bridge", "modal"}}
    return AppConfig(**known)


def modal_configured() -> bool:
    """Modal 认证凭证是否已填写（M1 Runner 接入后作为通道可用前提）。"""
    modal = get_settings().modal
    return bool(modal.token_id and modal.token_secret)


def workflows_dir() -> Path:
    """本地工作流目录（Desktop 版保存在 user/default/workflows/）。"""
    return Path(get_settings().local.comfy_root) / "user" / "default" / "workflows"


def input_dir() -> Path:
    """本地 ComfyUI 输入目录（LoadImage 等上传文件的查找根）。"""
    return Path(get_settings().local.comfy_root) / "input"


def custom_nodes_dir() -> Path:
    return Path(get_settings().local.comfy_root) / "custom_nodes"


DEFAULT_MODEL_CATEGORIES = (
    "checkpoints",
    "loras",
    "vae",
    "controlnet",
    "upscale_models",
    "embeddings",
    "diffusion_models",
    "text_encoders",
)


def model_dirs_resolved() -> dict[str, str]:
    """本地模型目录映射：显式配置优先，未配置分类回退 comfy_root/models/<分类>。"""
    local = get_settings().local
    base = Path(local.comfy_root) / "models"
    resolved = {cat: str(base / cat) for cat in DEFAULT_MODEL_CATEGORIES}
    for cat, path in local.models_dirs.items():
        resolved[cat] = path
    return resolved

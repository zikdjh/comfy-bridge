"""
在 Modal 上部署 ComfyUI：L40S GPU + 4 核 CPU + 32 GiB 内存。

一、数据卷持久化与 ComfyUI 目录的映射关系
------------------------------------------
数据卷 comfyui-data 挂载到容器 /data，启动时用 ComfyUI 官方参数
`--base-directory /data` 把整套目录树落到数据卷上，与 ComfyUI 自身目录一一对应；
模型同样持久化在数据卷，并按 ComfyUI 自身的 models/ 目录结构分类存放
（子目录结构与 folder_paths.py 注册的一致，见 MODEL_SUBDIRS，启动时自动建好）。

    /data（数据卷）                      ComfyUI 目录                    持久化内容
    ├── user/                         ComfyUI/user/                  ├ default/comfy.settings.json 前端设置
    │                                                               ├ default/workflows/          工作流
    │                                                               ├ comfyui.db                  工作流/资产数据库
    │                                                               └ users.json、comfyui.log 等
    ├── custom_nodes/                  ComfyUI/custom_nodes/          自定义节点
    ├── models/                        ComfyUI/models/                模型权重（checkpoints/loras/vae/
    │                                                               text_encoders/diffusion_models/…）
    ├── input/                         ComfyUI/input/                 上传的输入文件
    ├── output/                        ComfyUI/output/                生成结果
    ├── temp/                          ComfyUI/temp/                  临时文件
    └── config/extra_model_paths.yaml  ComfyUI/extra_model_paths.yaml 模型/节点搜索路径配置

说明：本版 ComfyUI 的数据库默认取“生效的 user 目录”下的 comfyui.db
（app/database/db.py: get_database_url），因此随 user/ 一并持久化。

二、启动时添加设置，默认不使用交互式界面
----------------------------------------
1. `--disable-auto-launch`：启动时不自动拉起浏览器交互界面，服务以无界面方式运行；
2. 启动时把 DEFAULT_SETTINGS 合并进 user/default/comfy.settings.json
   （只补缺、不覆盖你已改过的键）：默认不启用交互式菜单界面、开启工作流持久化；
3. custom_nodes/*/requirements.txt 的依赖在启动时自动补齐
   （节点文件在数据卷上，Python 依赖不在镜像里，冷启动时安装一次）；
4. 若数据卷存在 /config/launch_args.json（管理台「设置 → 启动命令」下发），
   冷启动时把其中的参数追加到启动命令；文件缺失或不合法则忽略。

三、使用
--------
部署：  modal deploy modal-runner/runner.py   # 常驻部署，输出固定 Web URL
调试：  modal serve  modal-runner/runner.py   # 临时应用，改码自动重载
停止：  modal app stop comfyui-l40s

下载模型（SDK/CLI 直接触发，云端直接落进数据卷）：
  modal run modal-runner/runner.py::download_model --source <HF仓库或URL> --subdir checkpoints
  本地 SDK：
      f = modal.Function.from_name("comfyui-l40s", "download_model")
      f.remote("<HF仓库或URL>", subdir="loras")
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import modal

# ------------------------------------------------------------------ 基本配置
APP_NAME = "comfyui-l40s"
# 部署的主数据卷固定为 comfyui-data；「数据卷管理」云端直下可指向任意卷——
# modal run 在本地导入本文件构建临时应用，导入前设置 CB_MODAL_VOLUME 即重定向挂载卷。
VOLUME_NAME = os.environ.get("CB_MODAL_VOLUME") or "comfyui-data"

DATA_ROOT = "/data"                 # 数据卷挂载点，同时是 ComfyUI 的基准目录
COMFY_DIR = "/root/ComfyUI"         # 镜像内 ComfyUI 安装目录（仓库根）
PORT = 8188

# 云端 ComfyUI 版本 pin：读 lock/comfyui_version.txt（桥接器「版本对齐」功能维护，
# 本地版本为权威；本文件只是消费方），锁文件缺失/为空时回退默认值。
# 改 pin 后需 modal deploy 重建镜像才生效（桥接器 POST /api/v1/nodes/version/align 会自动做）。
DEFAULT_COMFYUI_VERSION = "v0.37.4"


def _pinned_comfyui_version() -> str:
    lock = Path(__file__).resolve().parent / "lock" / "comfyui_version.txt"
    try:
        text = lock.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_COMFYUI_VERSION
    return text or DEFAULT_COMFYUI_VERSION


COMFYUI_VERSION = _pinned_comfyui_version()

# ComfyUI 模型目录结构（与 folder_paths.py 注册的 models/ 子目录一致）。
# 启动时在数据卷上按此结构建好分类目录，模型按类别对号入座存放。
MODEL_SUBDIRS = (
    "checkpoints", "configs", "loras", "vae",
    "text_encoders", "clip", "unet", "diffusion_models",
    "clip_vision", "style_models", "embeddings", "diffusers", "vae_approx",
    "controlnet", "t2i_adapter", "gligen", "upscale_models", "latent_upscale_models",
    "hypernetworks", "photomaker", "classifiers", "model_patches", "audio_encoders",
    "background_removal", "frame_interpolation", "geometry_estimation",
    "optical_flow", "detection",
)

INSTALL_CUSTOM_NODE_DEPS = True     # 启动时为数据卷上的自定义节点安装依赖

# 启动时写入 user/default/comfy.settings.json 的默认设置（已存在的键不会被覆盖）
DEFAULT_SETTINGS = {
    "Comfy.UseNewMenu": "Disabled",  # 默认不使用交互式菜单界面
    "Comfy.Workflow.Persist": True,  # 工作流草稿持久化到 user/ 下
}

# 数据卷里预置的 extra_model_paths.yaml 模板（首次启动生成，之后由你自由修改）
EXTRA_MODEL_PATHS_TEMPLATE = """\
# ComfyUI 额外模型 / 自定义节点搜索路径（位于数据卷，可自由修改）
# 用法见 ComfyUI/extra_model_paths.yaml.example；改完重启应用生效。
#
# my_models:
#     base_path: /data/models          # 主模型目录已随 /data/models 持久化；
#                                      # 这里可指向其他额外模型存放位置
#     checkpoints: checkpoints/
#     loras: loras/
#     vae: vae/
#     text_encoders: text_encoders/
#     diffusion_models: diffusion_models/
#
# more_nodes:
#     custom_nodes: /data/extra_custom_nodes

# 下面的空节占位用于保证本文件是合法 YAML：纯注释文件会被解析为 None，
# ComfyUI 的 load_extra_path_config 遍历 None 会直接崩溃。
# 可把它改成上面示例那样的真实配置，或保留为空节。
extra_paths:
"""

# -------------------------------------------------------------------- 镜像
image = (
    modal.Image.debian_slim(python_version="3.12")
    # 常用系统库：git（克隆/更新节点）、ffmpeg（视频类节点）、libgl1/libglib2.0-0（OpenCV）
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0")
    .run_commands(f"git clone --depth 1 --branch {COMFYUI_VERSION} https://github.com/comfyanonymous/ComfyUI.git {COMFY_DIR}")
    # requirements.txt 含 torch(CUDA 版)、comfyui-frontend-package 等全部依赖
    .run_commands(f"cd {COMFY_DIR} && python -m pip install --no-cache-dir -r requirements.txt")
    # download_model 云端直下模型用
    .uv_pip_install("huggingface_hub[hf_transfer]")
)

app = modal.App(APP_NAME)
data_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


# ------------------------------------------------------------------ 启动准备
def prepare_data_volume() -> None:
    """确保数据卷目录结构与 ComfyUI 目录一一对应，并预置配置。"""
    for sub in ("user", "custom_nodes", "models", "input", "output", "temp", "config"):
        (Path(DATA_ROOT) / sub).mkdir(parents=True, exist_ok=True)
    for sub in MODEL_SUBDIRS:  # 按 ComfyUI 模型目录结构建好分类子目录
        (Path(DATA_ROOT) / "models" / sub).mkdir(parents=True, exist_ok=True)
    seed_settings()
    seed_extra_model_paths()


def seed_settings() -> None:
    """启动时添加设置：合并进 user/default/comfy.settings.json，已有键不覆盖。"""
    path = Path(DATA_ROOT) / "user" / "default" / "comfy.settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
    merged = {**DEFAULT_SETTINGS, **current}
    if merged != current:
        path.write_text(json.dumps(merged, indent=4), encoding="utf-8")


def seed_extra_model_paths() -> None:
    """首次启动在数据卷生成模型/节点搜索路径配置模板。"""
    path = Path(DATA_ROOT) / "config" / "extra_model_paths.yaml"
    if not path.is_file():
        path.write_text(EXTRA_MODEL_PATHS_TEMPLATE, encoding="utf-8")


def install_custom_node_deps() -> None:
    """节点文件在数据卷上而 Python 依赖在镜像外，冷启动时按 requirements.txt 补齐。

    单节点限时 300s 并把结果打到 stdout（经桥接器控制台可见）：
    依赖大/坏的节点不再静默吞错，也不会耗尽 600s 冷启动预算。
    """
    node_root = Path(DATA_ROOT) / "custom_nodes"
    disabled = {
        p.name.removesuffix(".disabled")
        for p in node_root.glob("*.disabled")
        if p.is_dir()
    }
    for req in sorted(node_root.glob("*/requirements.txt")):
        name = req.parent.name
        if name in disabled or name.endswith(".disabled"):
            print(f"[comfybridge] {name}: 已停用，跳过依赖安装", flush=True)
            continue
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                check=False, timeout=300, capture_output=True, text=True,
            )
        except subprocess.TimeoutExpired:
            print(f"[comfybridge] {name}: 依赖安装超时(>300s)，跳过（节点可能不可用）", flush=True)
            continue
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            print(f"[comfybridge] {name}: 依赖安装失败 rc={r.returncode}: {tail[-1] if tail else ''}", flush=True)
        else:
            print(f"[comfybridge] {name}: 依赖就绪", flush=True)


def load_extra_launch_args() -> list[str]:
    """读取管理台下发的追加启动参数（/config/launch_args.json，字符串数组）。

    由桥接器「设置 → 启动命令」经 modal volume put 写入；文件缺失/损坏/内容
    不合法时一律忽略，退回默认参数启动，保证容器始终能起来。
    注意：不能按 startswith("--") 过滤——"标志+值"式参数（如 --cuda-device 0）
    的值 token 不以 -- 开头，过滤掉会让标志悬空、ComfyUI argparse 直接退出。
    合法性（无空白/引号/cmd 元字符）已由桥接器 PUT /launch 时校验，这里全量透传。
    """
    path = Path(DATA_ROOT) / "config" / "launch_args.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(a) for a in raw if isinstance(a, str) and a.strip()]


def launch_comfyui() -> None:
    cmd = [
        sys.executable, "main.py",
        "--listen", "0.0.0.0", "--port", str(PORT),
        # 启动设置：默认不使用交互式界面（不自动打开浏览器）
        "--disable-auto-launch",
        # 目录映射：整套目录树（含 models/）落数据卷
        "--base-directory", DATA_ROOT,
    ]

    # 模型/节点搜索路径配置：仅在解析出有效内容时才传入。
    # 纯注释/空文件会被 YAML 解析为 None，ComfyUI 的 load_extra_path_config 会崩溃。
    cfg = Path(DATA_ROOT) / "config" / "extra_model_paths.yaml"
    if cfg.is_file():
        import yaml  # 仅容器内使用（ComfyUI 依赖自带 PyYAML）

        try:
            has_content = yaml.safe_load(cfg.read_text(encoding="utf-8")) is not None
        except yaml.YAMLError:
            has_content = False
        if has_content:
            cmd += ["--extra-model-paths-config", str(cfg)]

    cmd += load_extra_launch_args()  # 管理台「设置 → 启动命令」下发的追加参数

    proc = subprocess.Popen(cmd, cwd=COMFY_DIR)

    # watchdog：ComfyUI 中途崩溃时立刻结束容器（否则容器空转到 12h 超时，
    # 端口只表现为 502，无任何日志线索）。进程退出 → os._exit 让 Modal 重建容器。
    def _watchdog() -> None:
        code = proc.wait()
        print(f"[comfybridge] ComfyUI 进程退出 code={code}，结束容器等待 Modal 重建", flush=True)
        os._exit(1 if code else 0)

    threading.Thread(target=_watchdog, name="comfyui-watchdog", daemon=True).start()


# ------------------------------------------------------------------ 云函数
@app.function(
    image=image,
    gpu="L40S",          # 1x NVIDIA L40S
    cpu=4,               # 4 核物理 CPU
    memory=32768,        # 32 GiB 内存（单位 MiB）
    volumes={DATA_ROOT: data_volume},
    timeout=86400,
    max_containers=1,    # 单容器：ComfyUI 队列本身串行，且保证 WS/HTTP 路由到同一实例
)
@modal.web_server(PORT, startup_timeout=600)
def comfyui() -> None:
    prepare_data_volume()
    if INSTALL_CUSTOM_NODE_DEPS:
        install_custom_node_deps()
    launch_comfyui()


# ------------------------------------------------- SDK/CLI 直接下载模型到数据卷
@app.function(
    image=image,
    volumes={DATA_ROOT: data_volume},
    timeout=7200,
)
def download_model(
    source: str,
    subdir: str = "checkpoints",
    filename: str | None = None,
    hf_token: str | None = None,
    dest: str | None = None,
) -> list[str]:
    """把模型直接下载进数据卷 /data/models/<subdir>/（HuggingFace 仓库或直链 URL）。

    source:   HuggingFace 仓库 ID（如 "black-forest-labs/FLUX.1-dev"）或模型文件直链
    subdir:   MODEL_SUBDIRS 之一，按 ComfyUI 模型分类落盘
    filename: 只下仓库内指定文件（支持通配符）；直链时用作保存文件名
    hf_token: 私有/门禁 HF 仓库令牌（也可改用 modal.Secret 注入 HF_TOKEN）
    dest:     数据卷内任意相对目录（如 "input"、"custom_nodes"）；给出时忽略 subdir，
              供「数据卷管理」自由下载用。必须相对路径，禁止 .. 与隐藏首段。

    调用：
      modal run modal-runner/runner.py::download_model --source <repo或URL> --subdir checkpoints
      modal run modal-runner/runner.py::download_model --source <URL> --dest input
      modal.Function.from_name("comfyui-l40s", "download_model").remote("<repo或URL>", subdir="loras")
    """
    if dest:
        rel = dest.replace("\\", "/").strip("/")
        if (
            not rel
            or rel.startswith("/")
            or ".." in rel.split("/")
            or rel.split("/")[0].startswith(".")
        ):
            raise ValueError(f"dest 必须是数据卷内的相对目录，收到: {dest!r}")
        target = Path(DATA_ROOT) / rel
    else:
        if subdir not in MODEL_SUBDIRS:
            raise ValueError(f"subdir 必须是 MODEL_SUBDIRS 之一，收到: {subdir}")
        target = Path(DATA_ROOT) / "models" / subdir
    target.mkdir(parents=True, exist_ok=True)

    if source.startswith(("http://", "https://")):
        name = filename or source.rsplit("/", 1)[-1]
        with urllib.request.urlopen(source) as resp, open(target / name, "wb") as f:
            shutil.copyfileobj(resp, f)
    else:
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
        from huggingface_hub import snapshot_download  # 仅镜像内可用，故在函数内导入

        snapshot_download(
            repo_id=source,
            local_dir=str(target),
            allow_patterns=[filename] if filename else None,
            token=hf_token,
        )

    data_volume.commit()  # 显式提交，供运行中的 ComfyUI reload 后识别
    return sorted(str(p.relative_to(DATA_ROOT)) for p in target.rglob("*") if p.is_file())

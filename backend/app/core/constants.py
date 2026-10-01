"""桥接器共享常量：数据面接管端点、生图/视频分类节点表（规划文档 §2.1 / §3.1）。"""

from __future__ import annotations

# 图片类输出节点（判定「生图请求」）
IMAGE_OUTPUT_NODES = {
    "SaveImage",
    "PreviewImage",
    "SaveImageWebsocket",
    "SaveImageWithAlpha",
}

# 视频类输出节点（判定「视频请求」→ video_backend 规则）
VIDEO_OUTPUT_NODES = {
    "VHS_VideoCombine",  # ComfyUI-VideoHelperSuite
    "SaveAnimatedWEBP",  # ComfyUI 内置
    "SaveAnimatedPNG",  # ComfyUI 内置
    "SaveWEBM",  # ComfyUI-VideoHelperSuite 旧版
    "SaveVideo",  # ComfyUI 内置（V3 assets 输出）
}

# 可从 inputs 读出 width/height 的 latent 节点（估算分辨率用）
LATENT_SIZE_NODES = {
    "EmptyLatentImage",
    "EmptySD3LatentImage",
    "EmptyHunyuanLatentVideo",
    "EmptyLatentVideo",
    "EmptyMochiLatentVideo",
    "EmptyCosmosLatentVideo",
    "EmptyLTXVLatentVideo",
}

# 模型组合框输入名 → 模型分类（云端专有模型判定、/object_info 合并共用）。
# 值为该输入可选模型所在的 models/ 子目录（含等价旧目录，如 unet ↔ diffusion_models）。
MODEL_INPUT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "ckpt_name": ("checkpoints",),
    "unet_name": ("diffusion_models", "unet"),
    "clip_name": ("text_encoders", "clip"),
    "clip_name1": ("text_encoders", "clip"),
    "clip_name2": ("text_encoders", "clip"),
    "clip_name3": ("text_encoders", "clip"),
    "clip_name4": ("text_encoders", "clip"),
    "vae_name": ("vae",),
    "lora_name": ("loras",),
    "control_net_name": ("controlnet", "t2i_adapter"),
    "style_model_name": ("style_models",),
    "clip_vision_name": ("clip_vision",),
    "model_name": ("upscale_models",),  # UpscaleModelLoader 等
}

# 视为模型权重的文件扩展名（未知加载器节点按 “*_name + 扩展名” 兜底识别）
MODEL_FILE_EXTS = {
    ".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx",
}

# Runner 固定部署的 GPU 型号（modal-runner/runner.py）；换型需同步改这里。
# 成本估算单价（cost）与 Modal 账单明细的 GPU 资源行匹配（modal_billing.report）共用。
GPU_TYPE = "L40S"

# 透传/回写时剥离的逐跳头（HTTP/1.1 RFC 7230 + host/content-length 由 httpx 重算）
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

/**
 * ComfyUI 官方启动参数目录（v0.37.4，本地与云端同版本）。
 *
 * 来源：docs.comfy.org「Startup flags」官方文档
 *      （https://docs.comfy.org/development/comfyui-server/startup-flags）
 *      与仓库内 comfy/cli_args.py（官方实现，含默认值与互斥分组）。
 * 说明文字为官方 help 的中文概括。
 */

export type ValueMode = "none" | "required" | "optional";
export type Target = "local" | "modal";

export interface LaunchFlagDef {
  /** 参数名（值参数的值可带 "=" 或拆成两个 token，见 buildTokens） */
  name: string;
  valueMode: ValueMode;
  /** select 型的可选取值 */
  options?: string[];
  /** 输入框占位提示 */
  placeholder?: string;
  /** 互斥组 id：同组参数同时只能启用一个（官方 parser 的 mutually exclusive group） */
  group?: string;
  category: string;
  targets: Target[];
  desc: string;
}

export const LAUNCH_FLAGS: LaunchFlagDef[] = [
  // —— 网络与目录 ——
  { name: "--enable-cors-header", valueMode: "optional", placeholder: "Origin，留空为 *", category: "网络与目录", targets: ["local"], desc: "开启跨域访问（值=允许的 Origin，留空允许全部）。" },
  { name: "--max-upload-size", valueMode: "required", placeholder: "MB，默认 100", category: "网络与目录", targets: ["local", "modal"], desc: "上传文件大小上限（MB），默认 100。" },
  { name: "--output-directory", valueMode: "required", placeholder: "绝对路径", category: "网络与目录", targets: ["local"], desc: "覆盖输出目录（优先于 --base-directory）。" },
  { name: "--input-directory", valueMode: "required", placeholder: "绝对路径", category: "网络与目录", targets: ["local"], desc: "覆盖输入目录（优先于 --base-directory）。" },
  { name: "--user-directory", valueMode: "required", placeholder: "绝对路径", category: "网络与目录", targets: ["local"], desc: "覆盖用户目录（设置/工作流所在，须为绝对路径）。" },

  // —— 设备 ——
  { name: "--cuda-device", valueMode: "required", placeholder: "如 0 或 0,1", category: "设备", targets: ["local", "modal"], desc: "本实例使用的 CUDA 设备（逗号分隔），其余设备不可见。" },
  { name: "--disable-cuda-malloc", valueMode: "none", category: "设备", targets: ["local", "modal"], desc: "关闭 cudaMallocAsync 显存分配器（torch≥2.0 默认开启）。" },

  // —— 全局精度 ——
  { name: "--force-fp32", valueMode: "none", group: "fp-global", category: "精度", targets: ["local", "modal"], desc: "全局强制 fp32（官方：若效果更好请反馈）。" },
  { name: "--force-fp16", valueMode: "none", group: "fp-global", category: "精度", targets: ["local", "modal"], desc: "全局强制 fp16（同时隐含 --fp16-unet）。" },

  // —— UNET 精度（互斥）——
  { name: "--fp32-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "扩散模型以 fp32 运行。" },
  { name: "--fp64-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "扩散模型以 fp64 运行（显存占用极高）。" },
  { name: "--bf16-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "扩散模型以 bf16 运行。" },
  { name: "--fp16-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "扩散模型以 fp16 运行。" },
  { name: "--fp8_e4m3fn-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "UNET 权重以 fp8_e4m3fn 存储。" },
  { name: "--fp8_e5m2-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "UNET 权重以 fp8_e5m2 存储。" },
  { name: "--fp8_e8m0fnu-unet", valueMode: "none", group: "unet", category: "精度", targets: ["local", "modal"], desc: "UNET 权重以 fp8_e8m0fnu 存储。" },

  // —— VAE 精度（互斥）——
  { name: "--fp16-vae", valueMode: "none", group: "vae", category: "精度", targets: ["local", "modal"], desc: "VAE 以 fp16 运行（可能出现全黑图）。" },
  { name: "--fp32-vae", valueMode: "none", group: "vae", category: "精度", targets: ["local", "modal"], desc: "VAE 以全精度 fp32 运行。" },
  { name: "--bf16-vae", valueMode: "none", group: "vae", category: "精度", targets: ["local", "modal"], desc: "VAE 以 bf16 运行。" },
  { name: "--cpu-vae", valueMode: "none", group: "vae", category: "精度", targets: ["local", "modal"], desc: "VAE 在 CPU 上运行。" },

  // —— 文本编码器精度（互斥）——
  { name: "--fp8_e4m3fn-text-enc", valueMode: "none", group: "text-enc", category: "精度", targets: ["local", "modal"], desc: "文本编码器权重以 fp8 (e4m3fn) 存储。" },
  { name: "--fp8_e5m2-text-enc", valueMode: "none", group: "text-enc", category: "精度", targets: ["local", "modal"], desc: "文本编码器权重以 fp8 (e5m2) 存储。" },
  { name: "--fp16-text-enc", valueMode: "none", group: "text-enc", category: "精度", targets: ["local", "modal"], desc: "文本编码器权重以 fp16 存储。" },
  { name: "--fp32-text-enc", valueMode: "none", group: "text-enc", category: "精度", targets: ["local", "modal"], desc: "文本编码器权重以 fp32 存储。" },
  { name: "--bf16-text-enc", valueMode: "none", group: "text-enc", category: "精度", targets: ["local", "modal"], desc: "文本编码器权重以 bf16 存储。" },
  { name: "--fp16-intermediates", valueMode: "none", category: "精度", targets: ["local", "modal"], desc: "实验性：节点间中间张量用 fp16（默认 fp32）。" },
  { name: "--force-channels-last", valueMode: "none", category: "精度", targets: ["local", "modal"], desc: "推理时强制 channels-last 内存布局。" },
  { name: "--supports-fp8-compute", valueMode: "none", category: "精度", targets: ["local", "modal"], desc: "视作设备支持 fp8 计算。" },
  { name: "--enable-triton-backend", valueMode: "none", category: "精度", targets: ["local", "modal"], desc: "启用 comfy-kitchen 的 Triton 后端（默认关闭）。" },

  // —— 预览 ——
  { name: "--preview-method", valueMode: "required", options: ["none", "auto", "latent2rgb", "taesd"], category: "预览", targets: ["local", "modal"], desc: "采样节点的预览方式，默认 none。" },
  { name: "--preview-size", valueMode: "required", placeholder: "像素，默认 512", category: "预览", targets: ["local", "modal"], desc: "预览图最大尺寸（像素），默认 512。" },

  // —— 缓存（互斥）——
  { name: "--cache-ram", valueMode: "optional", placeholder: "GB 阈值，可两个值", category: "缓存", targets: ["local", "modal"], desc: "按内存压力缓存（默认模式）；值=活跃缓存阈值[+非活跃阈值] GB，留空用默认比例。" },
  { name: "--cache-classic", valueMode: "none", group: "cache", category: "缓存", targets: ["local", "modal"], desc: "旧式激进缓存策略。" },
  { name: "--cache-lru", valueMode: "required", placeholder: "缓存条数 N", category: "缓存", targets: ["local", "modal"], desc: "LRU 缓存最多 N 个节点结果（更吃内存/显存）。" },
  { name: "--cache-none", valueMode: "none", group: "cache", category: "缓存", targets: ["local", "modal"], desc: "禁用节点结果缓存，每次运行重算（省内存换时间）。" },

  // —— 注意力（互斥）——
  { name: "--use-split-cross-attention", valueMode: "none", group: "attn", category: "注意力", targets: ["local", "modal"], desc: "split cross attention 优化（用 xformers 时忽略）。" },
  { name: "--use-quad-cross-attention", valueMode: "none", group: "attn", category: "注意力", targets: ["local", "modal"], desc: "sub-quadratic cross attention 优化（用 xformers 时忽略）。" },
  { name: "--use-pytorch-cross-attention", valueMode: "none", group: "attn", category: "注意力", targets: ["local", "modal"], desc: "使用 PyTorch 2.0 自带 cross attention。" },
  { name: "--use-sage-attention", valueMode: "none", group: "attn", category: "注意力", targets: ["local", "modal"], desc: "使用 sage attention。" },
  { name: "--use-flash-attention", valueMode: "none", group: "attn", category: "注意力", targets: ["local", "modal"], desc: "使用 FlashAttention。" },
  { name: "--use-ck-attention", valueMode: "none", group: "attn", category: "注意力", targets: ["local", "modal"], desc: "使用 Comfy Kitchen attention。" },
  { name: "--disable-xformers", valueMode: "none", category: "注意力", targets: ["local", "modal"], desc: "禁用 xformers。" },
  { name: "--force-upcast-attention", valueMode: "none", group: "upcast", category: "注意力", targets: ["local", "modal"], desc: "强制开启 attention 上采样（官方：可修复黑图，欢迎反馈）。" },
  { name: "--dont-upcast-attention", valueMode: "none", group: "upcast", category: "注意力", targets: ["local", "modal"], desc: "关闭全部 attention 上采样（仅调试用）。" },

  // —— 显存与内存 ——
  { name: "--gpu-only", valueMode: "none", group: "vram-mode", category: "显存与内存", targets: ["local", "modal"], desc: "全部（含文本编码器）驻留 GPU 运行。" },
  { name: "--highvram", valueMode: "none", group: "vram-mode", category: "显存与内存", targets: ["local", "modal"], desc: "模型用完不卸载，常驻显存。" },
  { name: "--lowvram", valueMode: "none", group: "vram-mode", category: "显存与内存", targets: ["local", "modal"], desc: "文本编码器改在 CPU 运行（动态显存模式下无效）。" },
  { name: "--novram", valueMode: "none", group: "vram-mode", category: "显存与内存", targets: ["local", "modal"], desc: "极低显存模式（lowvram 仍不够时）。" },
  { name: "--cpu", valueMode: "none", group: "vram-mode", category: "显存与内存", targets: ["local", "modal"], desc: "全部用 CPU 运行（很慢，仅排障）。" },
  { name: "--reserve-vram", valueMode: "required", placeholder: "GB", category: "显存与内存", targets: ["local", "modal"], desc: "为操作系统/其他软件预留的显存（GB）。" },
  { name: "--vram-headroom", valueMode: "required", placeholder: "GB", category: "显存与内存", targets: ["local", "modal"], desc: "DynamicVRAM 额外保留的显存余量（GB）。" },
  { name: "--async-offload", valueMode: "optional", placeholder: "流数，留空为 2", category: "显存与内存", targets: ["local", "modal"], desc: "异步权重卸载（Nvidia 默认开启），值=卸载流数。" },
  { name: "--disable-async-offload", valueMode: "none", group: "async-offload", category: "显存与内存", targets: ["local", "modal"], desc: "关闭异步权重卸载。" },
  { name: "--enable-dynamic-vram", valueMode: "none", group: "dyn-vram", category: "显存与内存", targets: ["local", "modal"], desc: "在默认未开启动态显存的系统上启用动态显存。" },
  { name: "--disable-dynamic-vram", valueMode: "none", group: "dyn-vram", category: "显存与内存", targets: ["local", "modal"], desc: "关闭动态显存，改用估算式模型加载。" },
  { name: "--fast-disk", valueMode: "none", group: "fast-disk", category: "显存与内存", targets: ["local", "modal"], desc: "优先用磁盘直载/卸载（NVMe 快盘时可更快）。" },
  { name: "--disable-fast-disk", valueMode: "none", group: "fast-disk", category: "显存与内存", targets: ["local", "modal"], desc: "禁用磁盘直载（覆盖 --fast-disk）。" },
  { name: "--disable-smart-memory", valueMode: "none", category: "显存与内存", targets: ["local", "modal"], desc: "激进卸载到内存，尽量不把模型留在显存。" },
  { name: "--disable-pinned-memory", valueMode: "none", category: "显存与内存", targets: ["local", "modal"], desc: "禁用锁页内存。" },
  { name: "--mmap-torch-files", valueMode: "none", group: "mmap", category: "显存与内存", targets: ["local", "modal"], desc: "加载 ckpt/pt 文件时使用 mmap。" },
  { name: "--disable-mmap", valueMode: "none", group: "mmap", category: "显存与内存", targets: ["local", "modal"], desc: "加载 safetensors 时不使用 mmap。" },
  { name: "--disable-cuda-graphs", valueMode: "none", category: "显存与内存", targets: ["local", "modal"], desc: "禁用 CUDA graphs。" },
  { name: "--disable-comfy-compiler", valueMode: "none", category: "显存与内存", targets: ["local", "modal"], desc: "禁用 Comfy 模型编译器（含 CUDA graph 子特性）。" },
  { name: "--force-non-blocking", valueMode: "none", category: "显存与内存", targets: ["local", "modal"], desc: "所有适用张量使用非阻塞操作（部分环境提速，可能引发问题）。" },

  // —— 性能与调试 ——
  { name: "--fast", valueMode: "optional", placeholder: "fp16_accumulation fp8_matrix_mult …", category: "性能与调试", targets: ["local", "modal"], desc: "未充分验证的优化开关：留空=全部启用；可列白名单（fp16_accumulation / fp8_matrix_mult / cublas_ops / autotune）。" },
  { name: "--deterministic", valueMode: "none", category: "性能与调试", targets: ["local", "modal"], desc: "尽量使用确定性算法（更慢，且不保证结果完全可复现）。" },
  { name: "--dont-print-server", valueMode: "none", category: "性能与调试", targets: ["local"], desc: "不打印服务端请求日志。" },
  { name: "--log-stdout", valueMode: "none", category: "性能与调试", targets: ["local"], desc: "常规输出改走 stdout（默认 stderr）。" },
  { name: "--verbose", valueMode: "optional", placeholder: "DEBUG…CRITICAL", category: "性能与调试", targets: ["local"], desc: "详细日志级别（留空=DEBUG）。" },

  // —— 节点与元数据 ——
  { name: "--disable-all-custom-nodes", valueMode: "none", category: "节点与元数据", targets: ["local", "modal"], desc: "不加载任何自定义节点（配合 --whitelist-custom-nodes 使用）。" },
  { name: "--disable-api-nodes", valueMode: "none", category: "节点与元数据", targets: ["local", "modal"], desc: "禁用 API 节点，并阻止前端访问互联网。" },
  { name: "--disable-metadata", valueMode: "none", category: "节点与元数据", targets: ["local", "modal"], desc: "输出文件不写入 prompt 元数据。" },
  { name: "--enable-manager", valueMode: "none", category: "节点与元数据", targets: ["local", "modal"], desc: "启用 ComfyUI-Manager 特性。" },
];

"""Modal 资源价格参考与每小时预估算。

价格来自项目参考文档 docs/ComfyBridge-参考-Modal云平台与SDK.html
（2026-09 抓取的 modal.com/pricing）。Modal 价格会调整，界面必须标注
「参考价，以官网/账单为准」，这里的数值只用于设置页预估与成本提示。

计费口径：GPU 按卡数计费（规格串 "H100:2" = 2 卡），CPU 按物理核心数，
内存按 GiB；三者都按秒计费，这里折算为 $/小时。存储（Volume）不随
运行容器计时，因此不计入「每小时运行成本」。
"""

from __future__ import annotations

import re

# 参考单价：$/GPU/小时（单卡）
GPU_HOURLY_USD: dict[str, float] = {
    "B300": 7.10,
    "B200": 6.25,
    "H200": 4.54,
    "H100": 3.95,
    "RTXPRO6000": 3.03,
    "A100-80GB": 2.50,
    "A100-40GB": 2.10,
    "L40S": 1.95,
    "A10": 1.10,
    "L4": 0.80,
    "T4": 0.59,
}

CPU_HOURLY_PER_CORE_USD = 0.04716   # $0.0000131 / 核 / 秒
MEMORY_HOURLY_PER_GIB_USD = 0.007992  # $0.00000222 / GiB / 秒

PRICING_SOURCE = "Modal 官网参考价（2026-09）；实际以 modal.com/pricing 与账单为准"

_COUNT_RE = re.compile(r":\s*(\d+)\s*$")


def parse_gpu_spec(spec: str) -> tuple[str, int]:
    """规格串 → (型号键, 卡数)。"""
    raw = (spec or "").strip().upper()
    count = 1
    match = _COUNT_RE.search(raw)
    if match:
        count = max(1, int(match.group(1)))
        raw = raw[: match.start()]
    return raw, count


def _gpu_key(model: str) -> str | None:
    """把用户输入归一到价格表键；兼容 NVIDIA 前缀、A100 内存后缀与 RTX 写法。"""
    key = re.sub(r"[\s_-]+", "", model).removeprefix("NVIDIA")
    if key.startswith("RTXPRO"):
        key = key[6:]
    if key in {"6000", "PRO6000"}:
        key = "RTXPRO6000"
    if key == "A100":
        return "A100-80GB"  # Modal 的 A100 默认可能升级到 80GB
    if key in {"A100-80G", "A10080GB", "A100-80GB"}:
        return "A100-80GB"
    if key in {"A100-40G", "A10040GB", "A100-40GB"}:
        return "A100-40GB"
    if key in {"RTX6000", "RTXPRO6000", "PRO6000"}:
        return "RTXPRO6000"
    return key if key in GPU_HOURLY_USD else None


def estimate_hourly(gpu: str, cpu: float, memory_mb: int) -> dict:
    """按配置估算容器运行每小时成本。未知 GPU 只计 CPU/内存，并标记 GPU 未定价。"""
    model, cards = parse_gpu_spec(gpu)
    key = _gpu_key(model)
    gpu_hourly = round(GPU_HOURLY_USD[key] * cards, 4) if key else None
    cpu_hourly = round(max(float(cpu), 0.0) * CPU_HOURLY_PER_CORE_USD, 4)
    memory_gib = max(int(memory_mb), 0) / 1024
    memory_hourly = round(memory_gib * MEMORY_HOURLY_PER_GIB_USD, 4)
    total = round((gpu_hourly or 0.0) + cpu_hourly + memory_hourly, 4)
    return {
        "gpu_model": key,
        "gpu_cards": cards,
        "gpu_hourly_usd": gpu_hourly,
        "cpu_hourly_usd": cpu_hourly,
        "memory_hourly_usd": memory_hourly,
        "total_hourly_usd": total,
        "total_daily_usd": round(total * 24, 2),
        "priced": key is not None,
        "source": PRICING_SOURCE,
        "note": "按容器持续运行 1 小时估算；空闲缩容到 0 不产生计算费，冷启动与保活窗口按实际秒数计费。",
    }


def catalog() -> dict:
    """价格表（设置页展示与前端换算共用）。"""
    return {
        "gpu": {k: v for k, v in GPU_HOURLY_USD.items()},
        "cpu_hourly_per_core_usd": CPU_HOURLY_PER_CORE_USD,
        "memory_hourly_per_gib_usd": MEMORY_HOURLY_PER_GIB_USD,
        "source": PRICING_SOURCE,
    }

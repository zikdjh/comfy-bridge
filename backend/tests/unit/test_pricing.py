"""Modal 资源每小时预估价：型号归一、多卡、CPU/内存拆分与未知型号降级。"""

from __future__ import annotations

import pytest

from app.core import pricing


def test_parse_gpu_spec_counts_cards():
    assert pricing.parse_gpu_spec("L40S") == ("L40S", 1)
    assert pricing.parse_gpu_spec("h100:2") == ("H100", 2)
    assert pricing.parse_gpu_spec("A100-80GB:4") == ("A100-80GB", 4)
    assert pricing.parse_gpu_spec("H100:0") == ("H100", 1)  # 0 卡无意义，按 1 卡


def test_default_l40s_hourly_estimate():
    out = pricing.estimate_hourly("L40S", 4, 32768)
    assert out["priced"] is True
    assert out["gpu_model"] == "L40S"
    assert out["gpu_cards"] == 1
    assert out["gpu_hourly_usd"] == pytest.approx(1.95)
    assert out["cpu_hourly_usd"] == pytest.approx(0.1886)
    assert out["memory_hourly_usd"] == pytest.approx(0.2557)
    assert out["total_hourly_usd"] == pytest.approx(2.3943)
    assert out["total_daily_usd"] == pytest.approx(57.46)


def test_multi_gpu_multiplies_only_gpu():
    one = pricing.estimate_hourly("H100", 8, 65536)
    two = pricing.estimate_hourly("H100:2", 8, 65536)
    assert two["gpu_hourly_usd"] == pytest.approx(one["gpu_hourly_usd"] * 2)
    assert two["cpu_hourly_usd"] == one["cpu_hourly_usd"]
    assert two["memory_hourly_usd"] == one["memory_hourly_usd"]


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("A100", "A100-80GB"),
        ("NVIDIA A100 80GB", "A100-80GB"),
        ("NVIDIA-H100", "H100"),
        ("RTX PRO 6000", "RTXPRO6000"),
    ],
)
def test_gpu_aliases(raw, key):
    assert pricing.estimate_hourly(raw, 1, 1024)["gpu_model"] == key


def test_unknown_gpu_still_returns_cpu_memory():
    out = pricing.estimate_hourly("MYSTERY-GPU", 2, 8192)
    assert out["priced"] is False
    assert out["gpu_hourly_usd"] is None
    assert out["total_hourly_usd"] == pytest.approx(
        out["cpu_hourly_usd"] + out["memory_hourly_usd"]
    )


def test_catalog_contains_all_documented_gpus():
    cat = pricing.catalog()
    assert {"L40S", "L4", "A100-80GB", "H100", "H200", "B200", "B300"} <= set(cat["gpu"])
    assert cat["cpu_hourly_per_core_usd"] > 0
    assert cat["memory_hourly_per_gib_usd"] > 0

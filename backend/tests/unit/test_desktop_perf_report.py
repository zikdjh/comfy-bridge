from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _checker() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "check_desktop_perf_report.py"
    spec = importlib.util.spec_from_file_location("check_desktop_perf_report", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cycle(number: int, ready_ms: int) -> dict:
    return {
        "cycle": number,
        "cold_ready_ms": ready_ms,
        "close_ms": 320,
        "port_closed": True,
        "process_gone": True,
        "children_gone": True,
        "timeline": [
            {
                "clock_s": 0.0,
                "tree_working_set_mb": 120.0,
                "processes": [{"cpu_seconds": 1.0}],
            },
            {
                "clock_s": 3.0,
                "tree_working_set_mb": 124.0,
                "processes": [{"cpu_seconds": 1.03}],
            },
            {
                "clock_s": 6.0,
                "tree_working_set_mb": 125.0,
                "processes": [{"cpu_seconds": 1.06}],
            },
        ],
    }


def _check(report: dict, **overrides: float) -> list[str]:
    checker = _checker()
    kwargs = {
        "cold_budget": 3000.0,
        "warm_budget": 1500.0,
        "close_budget": 1000.0,
        "cpu_budget": 3.0,
        "rss_budget": 350.0,
        "min_cycles": 2,
        **overrides,
    }
    return checker.check(report, **kwargs)


def _check_resource_only(report: dict) -> list[str]:
    checker = _checker()
    return checker.check(
        report,
        cold_budget=3000.0,
        warm_budget=1500.0,
        close_budget=1000.0,
        cpu_budget=3.0,
        rss_budget=350.0,
        min_cycles=2,
        resource_only=True,
    )


def test_healthy_report_passes() -> None:
    report = {"cycles": [_cycle(1, 2200), _cycle(2, 1100), _cycle(3, 1000)]}
    assert _check(report) == []


def test_cold_start_budget_uses_first_cycle_not_maximum() -> None:
    report = {"cycles": [_cycle(1, 1800), _cycle(2, 1450), _cycle(3, 1500)]}
    assert _check(report) == []

    report["cycles"][0] = _cycle(1, 3200)
    failures = _check(report)
    assert any("冷启动 3200ms" in item for item in failures)


def test_resource_and_close_regressions_are_reported() -> None:
    report = {"cycles": [_cycle(1, 2000), _cycle(2, 1000)]}
    broken = copy.deepcopy(report)
    broken["cycles"][1]["close_ms"] = 1500
    broken["cycles"][1]["children_gone"] = False
    timeline = broken["cycles"][1]["timeline"]
    timeline[1]["processes"][0]["cpu_seconds"] = 1.5
    timeline[1]["tree_working_set_mb"] = 400.0
    failures = _check(broken)
    assert any("关闭耗时" in item for item in failures)
    assert any("后台子进程仍在" in item for item in failures)
    assert any("空闲 CPU" in item for item in failures)
    assert any("RSS 峰值" in item for item in failures)


def test_too_few_cycles_fails() -> None:
    report = {"cycles": [_cycle(1, 2000)]}
    failures = _check(report)
    assert failures and "启动样本不足" in failures[0]


def test_resource_only_report_does_not_require_lifecycle_fields() -> None:
    timeline = _cycle(1, 2000)["timeline"]
    report = {"cycles": [{"cycle": 1, "timeline": timeline}]}
    assert _check_resource_only(report) == []


@pytest.mark.parametrize("field", ["port_closed", "process_gone", "children_gone"])
def test_lifecycle_failures_are_reported(field: str) -> None:
    report = {"cycles": [_cycle(1, 2000), _cycle(2, 1000)]}
    report["cycles"][0][field] = False
    failures = _check(report)
    assert failures

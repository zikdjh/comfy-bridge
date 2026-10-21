#!/usr/bin/env python3
"""检查桌面启动/资源采样报告，区分冷启动、热启动与关闭清理预算。"""

from __future__ import annotations

import argparse
import json
import statistics
from itertools import pairwise
from pathlib import Path
from typing import Any


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
    return ordered[index]


def check(report: dict[str, Any], *, cold_budget: float, warm_budget: float,
          close_budget: float, cpu_budget: float, rss_budget: float,
          min_cycles: int = 2, resource_only: bool = False) -> list[str]:
    cycles = report.get("cycles") or []
    failures: list[str] = []
    if len(cycles) < (1 if resource_only else min_cycles):
        return [f"启动样本不足：{len(cycles)} < {min_cycles}"]
    cold_values: list[float] = []
    for cycle in cycles:
        if not resource_only:
            cold_values.append(float(cycle["cold_ready_ms"]))
            if float(cycle["close_ms"]) > close_budget:
                failures.append(f"第 {cycle['cycle']} 轮关闭耗时 {cycle['close_ms']}ms 超过 {close_budget}ms")
            if not cycle.get("port_closed"):
                failures.append(f"第 {cycle['cycle']} 轮关闭后端口仍可访问")
            if not cycle.get("process_gone"):
                failures.append(f"第 {cycle['cycle']} 轮关闭后启动器进程仍在")
            if not cycle.get("children_gone"):
                failures.append(f"第 {cycle['cycle']} 轮关闭后后台子进程仍在")
        timeline = cycle.get("timeline") or []
        if not timeline:
            failures.append(f"第 {cycle['cycle']} 轮缺少资源时间线")
            continue
        cpu_util: list[float] = []
        for prev, cur in pairwise(timeline):
            prev_clock = prev.get("clock_s", prev.get("elapsed_s"))
            cur_clock = cur.get("clock_s", cur.get("elapsed_s"))
            if prev_clock is None or cur_clock is None:
                failures.append(f"第 {cycle['cycle']} 轮资源时间线缺少 clock_s/elapsed_s")
                break
            elapsed = float(cur_clock) - float(prev_clock)
            if elapsed <= 0:
                continue
            cur_processes = cur.get("processes") or []
            prev_processes = prev.get("processes") or []
            if not cur_processes or not prev_processes:
                failures.append(f"第 {cycle['cycle']} 轮资源时间线缺少进程 CPU 数据")
                break
            cpu_delta = sum(float(p["cpu_seconds"]) for p in cur_processes) - sum(
                float(p["cpu_seconds"]) for p in prev_processes
            )
            cpu_util.append(max(0.0, cpu_delta / elapsed * 100))
        if cpu_util and _pct(cpu_util, 0.95) > cpu_budget:
            failures.append(
                f"第 {cycle['cycle']} 轮空闲 CPU p95 {_pct(cpu_util, 0.95):.2f}% 超过 {cpu_budget}%"
            )
        rss_peak = max(
            float(s.get("tree_working_set_mb", s.get("working_set_mb", 0))) for s in timeline
        )
        if rss_peak > rss_budget:
            failures.append(
                f"第 {cycle['cycle']} 轮整树 RSS 峰值 {rss_peak:.1f}MB 超过 {rss_budget}MB"
            )
    if resource_only:
        print(f"cycles={len(cycles)} resource-only")
        return failures
    cold = cold_values[0]
    warm = cold_values[1:]
    warm_median = statistics.median(warm)
    p95 = _pct(cold_values, 0.95)
    if cold > cold_budget:
        failures.append(f"冷启动 {cold:.0f}ms 超过 {cold_budget:.0f}ms")
    if warm_median > warm_budget:
        failures.append(f"热启动中位数 {warm_median:.0f}ms 超过 {warm_budget:.0f}ms")
    if p95 > cold_budget:
        failures.append(f"启动 p95 {p95:.0f}ms 超过 {cold_budget:.0f}ms")
    print(
        f"cycles={len(cycles)} cold={cold:.0f}ms warm_median={warm_median:.0f}ms "
        f"p95={p95:.0f}ms"
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--cold-budget", type=float, default=3000)
    parser.add_argument("--warm-budget", type=float, default=1500)
    parser.add_argument("--close-budget", type=float, default=1000)
    parser.add_argument("--cpu-budget", type=float, default=3.0)
    parser.add_argument("--rss-budget", type=float, default=350.0)
    parser.add_argument("--min-cycles", type=int, default=2)
    parser.add_argument("--resource-only", action="store_true", help="只检查空闲资源，不要求启动/关闭字段")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    failures = check(
        report,
        cold_budget=args.cold_budget,
        warm_budget=args.warm_budget,
        close_budget=args.close_budget,
        cpu_budget=args.cpu_budget,
        rss_budget=args.rss_budget,
        min_cycles=args.min_cycles,
        resource_only=args.resource_only,
    )
    if failures:
        print("\n".join(f"FAIL {item}" for item in failures))
        return 1
    print("PASS 桌面启动、空闲资源与关闭清理预算")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

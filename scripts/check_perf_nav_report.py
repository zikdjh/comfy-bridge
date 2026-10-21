#!/usr/bin/env python3
"""校验导航性能报告：冷/热分组、中位数和容差，避免 n=3 的 p95 被单次抖动主导。"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def _p95(values: list[int]) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--tolerance", type=int, default=50)
    parser.add_argument("--min-samples", type=int, default=3)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    failures: list[str] = []
    for page in report:
        samples = page.get("samples", [])
        if len(samples) < args.min_samples:
            failures.append(f"{page['page']}: 样本不足（{len(samples)} < {args.min_samples}）")
            continue
        median = round(statistics.median(samples))
        # 采样顺序固定为“首轮冷加载 + 后续热路径”；不能按最大值推断冷启动。
        cold = samples[0]
        warm = samples[1:]
        warm_median = round(statistics.median(warm)) if warm else median
        p95 = _p95(samples)
        print(
            f"{page['page']}: n={len(samples)} p95={p95}ms median={median}ms "
            f"cold={cold}ms warm_median={warm_median}ms"
        )
        if p95 > args.budget + args.tolerance:
            failures.append(f"{page['page']}: p95 {p95}ms 超过 {args.budget}+{args.tolerance}ms")
        if warm_median > args.budget:
            failures.append(f"{page['page']}: 热路径中位数 {warm_median}ms 超过 {args.budget}ms")

    if failures:
        print("\n".join(f"FAIL {item}" for item in failures))
        return 1
    print(f"PASS 导航预算 {args.budget}ms（冷加载容差 {args.tolerance}ms）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

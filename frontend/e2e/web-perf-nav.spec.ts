import { test } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

const BASE = "http://localhost:15173";

const ROUTES = [
  { path: "/", label: "仪表盘", ready: /系统状态/ },
  { path: "/routing", label: "路由规则", ready: /路由规则/ },
  { path: "/nodes", label: "节点管理", ready: /节点管理/ },
  { path: "/workflows", label: "工作流", ready: /工作流/ },
  { path: "/models", label: "模型卷", ready: /模型卷/ },
  { path: "/volume", label: "数据卷", ready: /数据卷/ },
  { path: "/media", label: "媒体库", ready: /媒体库/ },
  { path: "/cost", label: "成本报表", ready: /成本报表/ },
  { path: "/logs", label: "日志", ready: /请求日志（拦截层共/ },
  { path: "/status", label: "系统状态", ready: /系统状态/ },
  { path: "/settings", label: "设置", ready: /设置/ },
];

function pct(values: number[], p: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
  return sorted[idx];
}

test("page navigation latency baseline", async ({ page }) => {
  test.setTimeout(180_000);
  await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });

  const samples: Record<string, number[]> = {};
  // 3 轮：首轮含页面 chunk 冷加载，后两轮为缓存命中路径，与路线图"至少三轮"口径一致
  for (let round = 0; round < 3; round++) {
    // 每轮从仪表盘复跑同一条路径，避免上一页面留下的加载/请求状态污染下一页面样本。
    await page.getByRole("menuitem", { name: /仪表盘/ }).click();
    await page.getByText(/系统状态/).first().waitFor({ state: "visible" });
    for (const { path, label, ready } of ROUTES) {
      const item = page.getByRole("menuitem", { name: new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });
      const t0 = Date.now();
      await item.click();
      await page.waitForURL(`**${path === "/" ? "/" : path}`);
      // 页面可操作口径：目标页真实内容已渲染；SSE 是长连接，不能用 networkidle 代表交互完成。
      await page.getByText(ready).first().waitFor({ state: "visible" });
      const ms = Date.now() - t0;
      (samples[label] ??= []).push(ms);
    }
  }

  const report = Object.entries(samples).map(([name, values]) => ({
    page: name,
    p50: Math.round(pct(values, 0.5)),
    p95: Math.round(pct(values, 0.95)),
    max: Math.max(...values),
    n: values.length,
    samples: values,
  }));
  const reportPath = process.env.PERF_NAV_REPORT ?? resolve("test-results", "web-perf-nav.json");
  writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf-8");
  console.log("PERF-NAV", JSON.stringify(report));
  const worstP95 = Math.max(...report.map((r) => r.p95));
  console.log(`PERF-NAV worst p95 = ${worstP95}ms (budget 300ms)`);
  // 预算判定放在 scripts/check_perf_nav_report.py，由样本量、冷/热分层与容差统一计算。
});

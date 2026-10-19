import { test } from "@playwright/test";

const BASE = "http://localhost:15173";

const ROUTES = [
  { path: "/", label: "仪表盘" },
  { path: "/routing", label: "路由规则" },
  { path: "/nodes", label: "节点管理" },
  { path: "/workflows", label: "工作流" },
  { path: "/models", label: "模型卷" },
  { path: "/volume", label: "数据卷" },
  { path: "/media", label: "媒体库" },
  { path: "/cost", label: "成本报表" },
  { path: "/logs", label: "日志" },
  { path: "/status", label: "系统状态" },
  { path: "/settings", label: "设置" },
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
    for (const { path, label } of ROUTES) {
      const item = page.getByRole("menuitem", { name: new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });
      const t0 = Date.now();
      await item.click();
      await page.waitForURL(`**${path === "/" ? "/" : path}`);
      await page.waitForLoadState("networkidle");
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
  }));
  console.log("PERF-NAV", JSON.stringify(report));
  const worstP95 = Math.max(...report.map((r) => r.p95));
  console.log(`PERF-NAV worst p95 = ${worstP95}ms (budget 300ms)`);
});

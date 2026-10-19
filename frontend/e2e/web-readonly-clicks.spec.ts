import { expect, test } from "@playwright/test";

const BASE = "http://localhost:15173";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
});

test("all navigation routes render without a blank main area", async ({ page }) => {
  const routes = [
    "/",
    "/routing",
    "/nodes",
    "/workflows",
    "/models",
    "/volume",
    "/media",
    "/cost",
    "/logs",
    "/status",
    "/settings",
  ];
  for (const route of routes) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    await expect(page.locator("main"), route).toBeVisible();
    await expect(page.locator("main"), route).not.toBeEmpty();
  }
  await page.goto(`${BASE}/no-such-page`);
  await expect(page.getByText("页面不存在", { exact: true })).toBeVisible();
});

test("dashboard shortcuts reach real pages", async ({ page }) => {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.getByRole("link", { name: "成本报表", exact: true }).click();
  await expect(page).toHaveURL(/\/cost$/);
  await page.getByRole("link", { name: /查看全部/ }).click();
  await expect(page).toHaveURL(/\/logs$/);
});

test("request filters, detail drawer and realtime switch remain usable", async ({ page }) => {
  await page.goto(`${BASE}/logs`, { waitUntil: "networkidle" });
  await page.getByText(/请求日志（拦截层共/).click();
  const backend = page.locator(".ant-card-extra .ant-select").first();
  await backend.click();
  await page.getByText("云端", { exact: true }).click();
  await expect(page.locator(".ant-table-row").first()).toBeVisible();
  await page.locator(".ant-table-row").first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(/请求详情/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  const realtime = page.getByRole("switch");
  const before = await realtime.isChecked();
  await realtime.click();
  await expect(realtime).toBeChecked({ checked: !before });
});

test("settings tabs and scan dialog are clickable", async ({ page }) => {
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  for (const name of ["本地", "启动命令", "桥接", "Modal", "护栏与预算", "设置同步"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute("aria-selected", "true");
  }
  await page.getByRole("tab", { name: "本地", exact: true }).click();
  await page.getByRole("button", { name: "扫描本机", exact: true }).click();
  const scan = page.getByRole("dialog");
  await expect(scan).toBeVisible();
  await expect(scan.getByRole("button", { name: /采用|选择/ }).first()).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(scan).toBeHidden();
});

test("cost, media and console read-only controls work", async ({ page }) => {
  await page.goto(`${BASE}/cost`, { waitUntil: "networkidle" });
  await page.getByText("按主模型", { exact: true }).click();
  await expect(page.getByText("按主模型", { exact: true })).toBeVisible();

  await page.goto(`${BASE}/media`, { waitUntil: "networkidle" });
  await page.getByText("视频", { exact: true }).first().click();
  await page.getByText("图片", { exact: true }).first().click();

  await page.goto(`${BASE}/logs`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "实时控制台", exact: true }).click();
  await page.getByText("左右", { exact: true }).click();
  await page.getByText("上下", { exact: true }).click();
  await page.getByText("自适应", { exact: true }).click();
});

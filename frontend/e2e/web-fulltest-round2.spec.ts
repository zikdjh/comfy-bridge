import { expect, test } from "@playwright/test";

const BASE = "http://localhost:15173";
const EVIDENCE_DIR = "C:/Users/z2351/.codex/visualizations/2026/10/15/01a13d51-a08d-7370-85c3-888b64255c42/ego/comfy-bridge-web-20261018";

test.use({ viewport: { width: 1440, height: 900 } });

test("deep links reload and unknown route 404", async ({ page }) => {
  for (const route of ["/nodes", "/logs", "/settings", "/no-such-page"]) {
    await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    if (route === "/no-such-page") {
      await expect(page.getByText("页面不存在", { exact: true })).toBeVisible();
      await expect(page.getByText("404", { exact: true }).first()).toBeVisible();
    } else {
      await expect(page.locator("main")).toBeVisible();
      await expect(page.locator("main")).not.toBeEmpty();
    }
    await page.screenshot({
      path: `${EVIDENCE_DIR}/pw-deep-${route.replaceAll("/", "_")}.png`,
      fullPage: true,
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);
    if (route === "/no-such-page") {
      await expect(page.getByText("页面不存在", { exact: true })).toBeVisible();
    } else {
      await expect(page.locator("main")).not.toBeEmpty();
    }
  }
});

test("request drawer refresh preserves filter", async ({ page }) => {
  await page.goto(`${BASE}/logs`);
  await page.getByRole("tab", { name: "请求日志" }).click();
  await page.locator(".ant-select").nth(0).click();
  await page.getByTitle("云端").click();
  await page.locator(".ant-select").nth(1).click();
  await page.getByTitle("完成").click();
  const firstRow = page.locator(".ant-table-tbody tr.clickable-row").first();
  await expect(firstRow).toBeVisible({ timeout: 20_000 });
  await firstRow.locator("td").first().click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("实际执行");
  await expect(drawer).toContainText("云端");
  await drawer.getByRole("button", { name: /刷新/ }).click();
  await expect(drawer).toBeVisible();
  await expect(drawer).not.toContainText("加载中");
  await page.screenshot({
    path: `${EVIDENCE_DIR}/pw-request-drawer.png`,
    fullPage: true,
  });
  await drawer.locator(".ant-drawer-close").click();
  await expect(page.locator(".ant-select").nth(0)).toContainText("云端");
  await expect(page.locator(".ant-select").nth(1)).toContainText("完成");
});

test("console clear and buffer limits", async ({ page }) => {
  await page.goto(`${BASE}/logs`);
  await page.getByRole("tab", { name: "实时控制台" }).click();
  const panes = page.locator(".ant-card");
  const local = panes.filter({ hasText: "本地 ComfyUI" }).first();
  await expect(local).toBeVisible();

  const limit = local.locator(".ant-select").last();
  await limit.click();
  await page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter({ hasText: "上限 200" }).click();
  await expect(limit).toContainText("上限 200");

  const clear = local.getByRole("button", { name: /清屏|清 空|清除/ }).first();
  await expect(clear).toBeVisible();
  await clear.click();
  await expect(local).toContainText(/0\s*行|0 \/|已清空|暂无/);

  await limit.click();
  await page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter({ hasText: "上限 5000" }).click();
  await expect(limit).toContainText("上限 5000");
  await page.screenshot({
    path: `${EVIDENCE_DIR}/pw-console-clear-buffer.png`,
    fullPage: true,
  });
});

test("ComfyUI page opens and exposes queue controls without generation", async ({
  page,
}) => {
  await page.goto("http://127.0.0.1:18189/", {
    waitUntil: "domcontentloaded",
  });
  await expect(page.locator("canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Queue Prompt" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole("button", { name: "View Queue" })).toBeVisible();
  await page.screenshot({
    path: `${EVIDENCE_DIR}/pw-comfyui-page.png`,
    fullPage: true,
  });
});

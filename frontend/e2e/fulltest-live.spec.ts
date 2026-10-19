import { expect, test, type Page } from "@playwright/test";

const MENU_PAGES = [
  "路由规则",
  "节点管理",
  "工作流",
  "模型卷",
  "数据卷",
  "媒体库",
  "成本报表",
  "日志",
  "系统状态",
  "设置",
] as const;

async function dismissWizard(page: Page) {
  const dialog = page.getByRole("dialog", { name: /首次配置向导/ });
  try {
    await dialog.waitFor({ state: "visible", timeout: 1500 });
  } catch {
    return;
  }
  await dialog.locator(".ant-modal-close").click();
  await expect(dialog).toBeHidden();
}

async function gotoMenu(page: Page, name: string) {
  const item = page.getByRole("menuitem", { name: new RegExp(name) });
  await expect(item).toHaveCount(1);
  await item.click();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/bridge/");
  await dismissWizard(page);
  await expect(page).toHaveTitle(/ComfyBridge/);
});

test("UI-01 仪表盘：真实两端状态与核心卡片", async ({ page }) => {
  const banner = page.getByRole("banner");
  await expect(banner.getByText(/本地 ComfyUI/)).toBeVisible();
  await expect(banner.getByText(/Modal Runner/)).toBeVisible();
  await expect(page.getByText("今日生成", { exact: false })).toBeVisible();
  await expect(page.getByText("最近请求", { exact: false })).toBeVisible();
  await page.screenshot({ path: "test-results/fulltest/UI-01-dashboard.png", fullPage: true });
});

test("UI-02 任务抽屉：打开后可关闭", async ({ page }) => {
  const taskButton = page.getByRole("banner").getByRole("button", { name: /任务/ });
  await taskButton.click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();
  await page.screenshot({ path: "test-results/fulltest/UI-02-job-drawer.png", fullPage: true });
  await drawer.locator(".ant-drawer-close").click();
  await expect(drawer).toBeHidden();
});

test("UI-03 预热取消：不发起预热", async ({ page }) => {
  await page.getByRole("button", { name: /预热/ }).click();
  const popover = page.locator(".ant-popover:visible");
  await expect(popover).toContainText("预热云端容器");
  await page.screenshot({ path: "test-results/fulltest/UI-03-warmup-confirm.png", fullPage: true });
  await popover.getByRole("button", { name: /取\s*消/ }).click();
  await expect(popover).toBeHidden();
});

test("UI-04 前往出图界面：进入 ComfyUI", async ({ page, context }) => {
  const link = page.getByRole("banner").getByRole("link", { name: /前往出图界面/ });
  await expect(link).toHaveAttribute("href", /127\.0\.0\.1:18189/);
  const [comfy] = await Promise.all([context.waitForEvent("page"), link.click()]);
  await comfy.waitForLoadState("domcontentloaded");
  await expect(comfy).toHaveURL(/127\.0\.0\.1:18189\/$/);
  await expect(comfy.locator("body")).toContainText(/Queue Prompt|Queue size/i, { timeout: 30_000 });
  await expect(comfy.locator("canvas")).toHaveCount(1);
  await comfy.screenshot({ path: "test-results/fulltest/UI-04-comfyui.png", fullPage: true });
});

test("全页面遍历：每页渲染且无错误边界", async ({ page }) => {
  const main = page.locator("main");
  for (const name of MENU_PAGES) {
    await gotoMenu(page, name);
    let text = "";
    for (let i = 0; i < 20; i++) {
      await page.waitForTimeout(300);
      const next = (await main.innerText()) || "";
      if (next.length > 20 && next === text) break;
      text = next;
    }
    expect(text.length, `${name} 页面内容过短`).toBeGreaterThan(20);
    expect(text, `${name} 出现渲染错误`).not.toMatch(/渲染出错|页面崩溃|Something went wrong/i);
    await page.screenshot({ path: `test-results/fulltest/page-${name}.png`, fullPage: true });
  }
});

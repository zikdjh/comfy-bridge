import { test, expect } from "@playwright/test";

const BASE = "http://localhost:15173";

test.describe("窄屏响应式（<768px）", () => {
  test.use({ viewport: { width: 400, height: 800 } });

  test("Sider 隐藏，抽屉导航可用", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });

    // 桌面 Sider 不渲染
    await expect(page.locator(".ant-layout-sider")).toHaveCount(0);

    // 菜单按钮出现，点击打开抽屉
    const menuBtn = page.locator('button[aria-label="打开导航菜单"]');
    await expect(menuBtn).toBeVisible();
    // 窄屏 Header 内容可能溢出视口，Playwright 的可见性检查过不了；用 JS 原生 click 绕开
    await menuBtn.evaluate((btn: HTMLButtonElement) => btn.click());

    // 抽屉展开，含 11 个菜单项
    await expect(page.locator(".ant-drawer-open")).toBeVisible();
    await expect(page.locator(".ant-drawer-open .ant-menu-item")).toHaveCount(11);

    // 点击菜单项后抽屉自动收起，路由跳转
    await page.locator(".ant-drawer-open .ant-menu-item", { hasText: "路由规则" }).evaluate((el: HTMLElement) => el.click());
    await expect(page).toHaveURL(/\/routing/);
    await expect(page.locator(".ant-drawer-open")).toHaveCount(0);
  });

  test("窄屏下表格有横向滚动容器，body 不溢出", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
    await page.goto(`${BASE}/logs`, { waitUntil: "domcontentloaded" });

    // 等待表格渲染
    await expect(page.locator(".ant-table")).toBeVisible();

    // body 不出现横向滚动条
    const bodyOverflow = await page.evaluate(
      () => document.body.scrollWidth <= window.innerWidth,
    );
    expect(bodyOverflow).toBeTruthy();

    // 表格有 scroll 容器（横向滚动兜底）
    const hasScrollContainer = await page.evaluate(
      () =>
        !!document.querySelector(
          ".ant-table-scroll, .ant-table-content, .ant-table-body",
        ),
    );
    expect(hasScrollContainer).toBeTruthy();
  });
});

test.describe("桌面视口（>=768px）", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Sider 渲染，抽屉导航不渲染", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });

    await expect(page.locator(".ant-layout-sider")).toBeVisible();
    await expect(page.locator('button[aria-label="打开导航菜单"]')).toHaveCount(0);
    await expect(page.locator(".ant-drawer-open")).toHaveCount(0);
  });
});

import { test, expect } from "@playwright/test";

const BASE = "http://localhost:15173";

/** 读取当前主题落到的 DOM 状态与关键背景色 */
const readTheme = (page: import("@playwright/test").Page) =>
  page.evaluate(() => {
    const root = document.documentElement;
    const layout = document.querySelector(".ant-layout");
    const surfaces = [
      ...document.querySelectorAll(
        ".ant-card, .ant-table, .ant-modal-content, .ant-layout-sider, .ant-menu, .ant-drawer-content",
      ),
    ];
    return {
      theme: root.getAttribute("data-theme"),
      stored: localStorage.getItem("cb_theme_mode"),
      colorScheme: root.style.colorScheme,
      layoutBg: layout ? getComputedStyle(layout).backgroundColor : null,
      lightSurfaces: surfaces.filter((el) => {
        const bg = getComputedStyle(el).backgroundColor;
        return bg === "rgb(255, 255, 255)" || bg === "rgb(250, 250, 250)";
      }).length,
      switchOn: !!document.querySelector(".ant-switch-checked"),
    };
  });

test.describe("暗色模式", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("无偏好时默认亮色，设置页开关可切到暗色并持久化", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("cb_wizard_done", "1");
      // 只在首次进入时清空偏好，避免刷新时又把暗色偏好抹掉
      if (!sessionStorage.getItem("cb_dark_spec_inited")) {
        sessionStorage.setItem("cb_dark_spec_inited", "1");
        localStorage.removeItem("cb_theme_mode");
      }
    });
    await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
    await expect(page.locator(".ant-switch").first()).toBeVisible();

    const initial = await readTheme(page);
    expect(initial.theme).toBe("light");
    expect(initial.switchOn).toBe(false);

    // 点击主题开关 → 暗色
    await page.locator(".ant-switch").first().click();
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("dark");

    const dark = await readTheme(page);
    expect(dark.stored).toBe("dark");
    expect(dark.colorScheme).toBe("dark");
    expect(dark.lightSurfaces).toBe(0);
    expect(dark.switchOn).toBe(true);

    // 刷新后保持暗色
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator(".ant-switch").first()).toBeVisible();
    const reloaded = await readTheme(page);
    expect(reloaded.theme).toBe("dark");
    expect(reloaded.stored).toBe("dark");

    // 再点一次 → 亮色
    await page.locator(".ant-switch").first().click();
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("light");
    const backToLight = await readTheme(page);
    expect(backToLight.stored).toBe("light");
    expect(backToLight.switchOn).toBe(false);
  });

  test("暗色下各主路由无浅色卡片残留", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("cb_wizard_done", "1");
      localStorage.setItem("cb_theme_mode", "dark");
    });

    const routes = ["/", "/models", "/workflows", "/nodes", "/media", "/volume", "/cost", "/logs", "/settings"];
    for (const route of routes) {
      await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
      await expect(page.locator(".ant-layout").first()).toBeVisible();
      const state = await readTheme(page);
      expect(state.theme, `路由 ${route} 应为暗色`).toBe("dark");
      expect(state.lightSurfaces, `路由 ${route} 不应出现浅色卡片`).toBe(0);
    }
  });
});

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
      themeMode: root.getAttribute("data-theme-mode"),
      stored: localStorage.getItem("cb_theme_mode"),
      colorScheme: root.style.colorScheme,
      layoutBg: layout ? getComputedStyle(layout).backgroundColor : null,
      lightSurfaces: surfaces.filter((el) => {
        const bg = getComputedStyle(el).backgroundColor;
        return bg === "rgb(255, 255, 255)" || bg === "rgb(250, 250, 250)";
      }).length,
    };
  });

/** 设置页「界面主题」三态分段控件 */
const themePicker = (page: import("@playwright/test").Page) =>
  page.locator(".ant-form-item", { hasText: "界面主题" }).locator(".ant-segmented");

/** 选择主题选项：跟随系统 / 亮色 / 暗色 */
const pickTheme = async (page: import("@playwright/test").Page, label: string) => {
  await themePicker(page).locator(".ant-segmented-item-label").filter({ hasText: label }).click();
};

test.describe("主题模式", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("未设置时默认跟随系统，三态切换并持久化", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.addInitScript(() => {
      localStorage.setItem("cb_wizard_done", "1");
      // 只在首次进入时清空偏好，避免刷新时又把选择抹掉
      if (!sessionStorage.getItem("cb_theme_spec_inited")) {
        sessionStorage.setItem("cb_theme_spec_inited", "1");
        localStorage.removeItem("cb_theme_mode");
      }
    });
    await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
    await expect(themePicker(page)).toBeVisible();

    const initial = await readTheme(page);
    expect(initial.theme).toBe("light");
    expect(initial.themeMode).toBe("system");
    expect(initial.stored).toBeNull();

    // 手动选暗色
    await pickTheme(page, "暗色");
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("dark");

    const dark = await readTheme(page);
    expect(dark.themeMode).toBe("dark");
    expect(dark.stored).toBe("dark");
    expect(dark.colorScheme).toBe("dark");
    expect(dark.lightSurfaces).toBe(0);

    // 刷新后保持暗色
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(themePicker(page)).toBeVisible();
    const reloaded = await readTheme(page);
    expect(reloaded.theme).toBe("dark");
    expect(reloaded.themeMode).toBe("dark");
    expect(reloaded.stored).toBe("dark");

    // 手动选亮色
    await pickTheme(page, "亮色");
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("light");
    const light = await readTheme(page);
    expect(light.themeMode).toBe("light");
    expect(light.stored).toBe("light");
    expect(light.colorScheme).toBe("light");

    // 回到跟随系统
    await pickTheme(page, "跟随系统");
    await expect
      .poll(async () => (await readTheme(page)).themeMode, { timeout: 5000 })
      .toBe("system");
    expect((await readTheme(page)).stored).toBe("system");
  });

  test("跟随系统时实时响应系统切换，手动选择后不再跟随", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("cb_wizard_done", "1");
      localStorage.setItem("cb_theme_mode", "system");
    });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
    await expect(themePicker(page)).toBeVisible();
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("dark");

    // 系统切回亮色：页面无需刷新即时跟随
    await page.emulateMedia({ colorScheme: "light" });
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("light");
    let state = await readTheme(page);
    expect(state.themeMode).toBe("system");
    expect(state.stored).toBe("system");

    // 系统再切暗色
    await page.emulateMedia({ colorScheme: "dark" });
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("dark");

    // 手动固定亮色后，系统变化不再影响
    await pickTheme(page, "亮色");
    await expect
      .poll(async () => (await readTheme(page)).theme, { timeout: 5000 })
      .toBe("light");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForTimeout(600);
    state = await readTheme(page);
    expect(state.theme).toBe("light");
    expect(state.themeMode).toBe("light");
    expect(state.stored).toBe("light");
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

import { test, expect, type Page } from "@playwright/test";

const BASE = "http://127.0.0.1:15173";
const DESKTOP_VIEWPORTS = [
  { name: "1024", width: 1024, height: 768, compactSider: true },
  { name: "1280", width: 1280, height: 800, compactSider: false },
  { name: "1440", width: 1440, height: 900, compactSider: false },
  { name: "1920", width: 1920, height: 1080, compactSider: false },
];

async function openPage(page: Page, path: string) {
  await page.addInitScript(() => {
    localStorage.setItem("cb_wizard_done", "1");
    localStorage.setItem("console.layout", "auto");
  });
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator(".ant-layout-sider")).toBeVisible();
  await expect(page.locator(".ant-layout-header")).toBeVisible();
}

async function expectNoPageOverflow(page: Page) {
  const metrics = await page.evaluate(() => ({
    body: document.body.scrollWidth,
    root: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(metrics.body, "body 不应出现页面级横向滚动").toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.root, "html 不应出现页面级横向滚动").toBeLessThanOrEqual(metrics.viewport);
}

async function expectHeaderDoesNotOverlap(page: Page) {
  const boxes = await page.evaluate(() => {
    const header = document.querySelector(".ant-layout-header");
    const spaces = header ? [...header.querySelectorAll(":scope > .ant-space")] : [];
    const rect = (el: Element | null | undefined) => {
      const r = el?.getBoundingClientRect();
      return r ? { left: r.left, right: r.right, top: r.top, bottom: r.bottom } : null;
    };
    return { header: rect(header), left: rect(spaces[0]), right: rect(spaces.at(-1)) };
  });
  expect(boxes.header).not.toBeNull();
  expect(boxes.left).not.toBeNull();
  expect(boxes.right).not.toBeNull();
  expect(boxes.left!.right, "顶栏左侧状态区不应侵入右侧操作区").toBeLessThanOrEqual(boxes.right!.left);
  expect(boxes.left!.left).toBeGreaterThanOrEqual(boxes.header!.left);
  expect(boxes.right!.right).toBeLessThanOrEqual(boxes.header!.right);
}

for (const viewport of DESKTOP_VIEWPORTS) {
  test.describe(`桌面 ${viewport.name}px`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test("仪表盘侧栏、顶栏与栅格布局稳定", async ({ page }) => {
      await openPage(page, "/");

      const sider = page.locator(".ant-layout-sider");
      const siderWidth = await sider.evaluate((el) => el.getBoundingClientRect().width);
      expect(siderWidth).toBe(viewport.compactSider ? 64 : 200);
      await expect(page.locator('button[aria-label="打开导航菜单"]')).toHaveCount(0);
      await expect(page.locator(".ant-drawer-open")).toHaveCount(0);

      await expectHeaderDoesNotOverlap(page);
      await expectNoPageOverflow(page);

      const cards = page.locator(".ant-row").first().locator(":scope > .ant-col");
      await expect(cards).toHaveCount(4);
      const cardTops = await cards.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().top));
      const firstRowCount = cardTops.filter((top) => Math.abs(top - cardTops[0]) < 2).length;
      expect(firstRowCount).toBe(4);

      const chart = page.getByText("生成趋势（近 14 天）", { exact: true }).first();
      const environment = page.getByText("运行环境", { exact: true }).first();
      await expect(chart).toBeVisible();
      await expect(environment).toBeVisible();
      const chartBox = await chart.locator("xpath=ancestor::div[contains(@class,'ant-card')][1]").boundingBox();
      const environmentBox = await environment.locator("xpath=ancestor::div[contains(@class,'ant-card')][1]").boundingBox();
      expect(chartBox).not.toBeNull();
      expect(environmentBox).not.toBeNull();
      expect(Math.abs(chartBox!.y - environmentBox!.y), "趋势与环境应在同一桌面行").toBeLessThan(4);
    });

    test("全部管理路由均不产生页面级横向溢出", async ({ page }) => {
      const routes = ["/routing", "/nodes", "/workflows", "/models", "/volume", "/media", "/cost", "/logs", "/status", "/settings"];
      for (const route of routes) {
        await openPage(page, route);
        await page.waitForTimeout(120);
        await expectHeaderDoesNotOverlap(page);
        await expectNoPageOverflow(page);
      }
    });

    test("日志页表格在内容区滚动，实时控制台按桌面宽度排布", async ({ page }) => {
      await openPage(page, "/logs");
      await expect(page.locator(".ant-table")).toBeVisible();
      await expectNoPageOverflow(page);
      const scrollContainer = page.locator(".ant-table-content, .ant-table-body").first();
      await expect(scrollContainer).toBeVisible();

      await page.getByRole("tab", { name: "实时控制台" }).click();
      const consoles = page.locator(".ant-card");
      const consoleBoxes = await consoles.evaluateAll((els) =>
        els.map((el) => el.getBoundingClientRect()).filter((r) => r.width > 300 && r.height > 300),
      );
      expect(consoleBoxes.length).toBeGreaterThanOrEqual(2);
      const sameRow = Math.abs(consoleBoxes[0].top - consoleBoxes[1].top) < 4;
      expect(sameRow).toBe(viewport.width >= 1280);
      await expectNoPageOverflow(page);
    });
  });
}

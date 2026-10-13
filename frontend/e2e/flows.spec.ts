/**
 * 全栈 E2E 核心链路（隔离的离线默认配置：本地 ComfyUI 未连接、Modal 未配置、
 * 无对齐历史、无任务）。
 *
 * 覆盖规划文档 §10 e2e 行的「管理台可用性」口径：首次配置向导契约、仪表盘核心
 * 元素、全部页面可渲染无错误边界、节点管理双 Tab、设置多 Tab 与任务抽屉
 * （JobDrawer）。导航一律经侧边栏菜单（不依赖具体路由路径）；断言基于离线空态
 * 的确定性契约。
 */
import { expect, test, type Page } from "@playwright/test";

// 侧边栏菜单（accessible name 含图标前缀，如 "cluster 节点管理"，用正则匹配）
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

const WIZARD = /首次配置向导/;

/**
 * 空配置下 Shell 会在加载约 600ms 后自动弹「首次配置向导」（每个测试都是全新
 * localStorage，必然弹）；后续断言前先按 Esc 关掉，避免 ant-modal-wrap 拦截点击。
 */
async function dismissWizard(page: Page) {
  const dlg = page.getByRole("dialog", { name: WIZARD });
  try {
    await dlg.waitFor({ state: "visible", timeout: 6000 });
  } catch {
    return; // 未弹出（向导已被跳过）
  }
  await dlg.locator(".ant-modal-close").click();
  await expect(dlg).toBeHidden();
}

/** 从仪表盘出发点击侧边栏菜单项。 */
async function gotoMenu(page: Page, name: string) {
  const item = page.getByRole("menuitem", { name: new RegExp(name) });
  await expect(item).toHaveCount(1);
  await item.click();
}

test("首次配置向导：空配置自动弹出、可跳过且本会话不再打扰", async ({ page }) => {
  await page.goto("/bridge/");
  const dlg = page.getByRole("dialog", { name: WIZARD });
  await expect(dlg).toBeVisible();
  await dlg.locator(".ant-modal-close").click();
  await expect(dlg).toBeHidden();
  // 跳过写入 localStorage：本会话内切换页面不再弹出
  await gotoMenu(page, "节点管理");
  await expect(page.getByRole("dialog", { name: WIZARD })).toHaveCount(0);
});

test("仪表盘：侧边栏、状态横幅与核心卡片渲染", async ({ page }) => {
  await page.goto("/bridge/");
  await dismissWizard(page);
  await expect(page).toHaveTitle(/ComfyBridge/);
  await expect(page.getByRole("menuitem", { name: /仪表盘/ })).toBeVisible();
  // 状态横幅（scope 到 banner，避免与运行环境表的重名文案歧义）
  const banner = page.getByRole("banner");
  await expect(banner.getByText(/本地 ComfyUI/)).toBeVisible();
  await expect(banner.getByText(/Modal Runner/)).toBeVisible();
  await expect(banner.getByText(/未连接/)).toBeVisible();
  // 核心卡片
  await expect(page.getByText("今日生成", { exact: false })).toBeVisible();
  await expect(page.getByText("运行中任务", { exact: false })).toBeVisible();
  await expect(page.getByText("最近请求", { exact: false })).toBeVisible();
});

test("全页面遍历：每个菜单页有内容且无错误边界", async ({ page }) => {
  await page.goto("/bridge/");
  await dismissWizard(page);
  const main = page.locator("main");
  for (const name of MENU_PAGES) {
    await gotoMenu(page, name);
    // 页面数据异步加载（React Query），等 main 文本连续两次采样一致再断言
    let text = "";
    // 25 × 400ms = 10s：节点管理在隔离配置下要等本地/云端探测超时后才出实质内容
    for (let i = 0; i < 25; i++) {
      const prev = text;
      await page.waitForTimeout(400);
      text = (await main.innerText()) || "";
      if (text.length > 20 && text === prev) break; // 有实质内容且不再变化才算稳定
    }
    expect(text.length, `${name} 页面内容过短`).toBeGreaterThan(20);
    expect(text, `${name} 出现渲染错误`).not.toMatch(/渲染出错|页面崩溃|Something went wrong/i);
  }
});

test("节点管理：本地清单空态、空对齐历史与差异矩阵 Tab", async ({ page }) => {
  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "节点管理");
  // 离线默认配置：本地清单显示未连接错误面板
  await expect(page.getByText("本地 ComfyUI 未连接")).toBeVisible();
  // 隔离配置目录无对齐历史 → 历史折叠面板不渲染（组件空态契约）
  await expect(page.getByRole("button", { name: /对齐历史/ })).toHaveCount(0);
  // 切换差异矩阵 Tab：面板有内容
  await page.getByRole("tab", { name: "差异矩阵" }).click();
  const panel = page.locator(".ant-tabs-tabpane-active");
  await expect(panel).not.toHaveText("");
});

test("设置：六 Tab、设置同步面板与任务抽屉空态", async ({ page }) => {
  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "设置");
  for (const tab of ["本地", "启动命令", "桥接", "Modal", "护栏与预算", "设置同步"]) {
    await expect(page.getByRole("tab", { name: tab })).toBeVisible();
  }
  await page.getByRole("tab", { name: "设置同步" }).click();
  await expect(page.getByText("两端设置差异（键级）")).toBeVisible();
  // 全局 JobDrawer（横幅「任务」按钮）：无任务时显示空态
  const drawerBtn = page.getByRole("banner").getByRole("button", { name: /任务/ });
  await expect(drawerBtn).toHaveCount(1);
  await drawerBtn.click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("暂无任务");
});

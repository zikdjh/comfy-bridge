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

test("首次配置向导：保存失败时在原位给出可行动建议", async ({ page }) => {
  await page.route("**/api/v1/config", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Token not found" }),
    });
  });
  await page.goto("/bridge/");
  const dlg = page.getByRole("dialog", { name: WIZARD });
  await expect(dlg).toBeVisible();
  await dlg.getByRole("button", { name: "开始配置", exact: true }).click();
  await dlg.getByPlaceholder("ak-…").fill("ak-test");
  await dlg.getByPlaceholder("as-…").fill("as-test");
  await dlg.getByRole("button", { name: "保存并下一步" }).click();
  const guidance = dlg.getByTestId("wizard-actionable-error");
  await expect(guidance).toContainText("Modal 凭证不可用");
  await expect(guidance).toContainText("设置 → Modal");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("仪表盘：侧边栏、状态横幅与核心卡片渲染", async ({ page }) => {
  await page.goto("/bridge/");
  await dismissWizard(page);
  await expect(page).toHaveTitle(/ComfyBridge/);
  await expect(page.getByRole("menuitem", { name: /仪表盘/ })).toBeVisible();
  // 状态横幅（scope 到 banner，避免与运行环境表的重名文案歧义）
  const banner = page.getByRole("banner");
  await expect(banner.getByText("本地", { exact: true })).toBeVisible();
  await expect(banner.getByText("Modal", { exact: true })).toBeVisible();
  await expect(banner.getByText(/未连接/)).toBeVisible();
  // 核心卡片
  await expect(page.getByText("今日生成", { exact: true })).toBeVisible();
  await expect(page.getByText("运行中任务", { exact: true })).toBeVisible();
  await expect(page.getByText("最近请求", { exact: true })).toBeVisible();
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
  for (const tab of [
    "本地",
    "启动命令",
    "桥接",
    "Modal",
    "护栏与预算",
    "设置同步",
    "数据安全",
    "检查更新",
  ]) {
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

test("设置：数据安全页签可备份、下载、清理和暂存恢复", async ({ page }) => {
  const backupName = "ComfyBridge-backup-20261021-120000-test.zip";
  let backups = [
    {
      name: backupName,
      size: 2 * 1024 * 1024,
      created_ms: Date.now(),
      reason: "manual",
    },
  ];
  let cleanupCalls = 0;
  let restoreCalls = 0;
  await page.route("**/api/v1/maintenance", async (route) => {
    await route.fulfill({
      json: {
        retention: { days: 90 },
        backups,
        restore_pending: restoreCalls > 0,
        last_restore: null,
        data_dir: "C:/data",
        backup_dir: "C:/backups",
        release: {
          version: "v0.1.0-beta.8",
          previous_version: "untracked",
          installed_at_ms: Date.now(),
          version_file: "C:/ComfyBridge/version.txt",
          history: [],
          upgrade_backups: [],
          last_self_check: {
            ok: true,
            version: "v0.1.0-beta.8",
            checked_at_ms: Date.now(),
            checks: [],
            error: null,
          },
          packaged: true,
        },
      },
    });
  });
  await page.route("**/api/v1/maintenance/backup", async (route) => {
    backups = [
      {
        name: "ComfyBridge-backup-new.zip",
        size: 1024,
        created_ms: Date.now(),
        reason: "manual",
      },
      ...backups,
    ];
    await route.fulfill({
      json: {
        name: "ComfyBridge-backup-new.zip",
        size: 1024,
        path: "C:/backups/ComfyBridge-backup-new.zip",
        contains_secrets: true,
      },
    });
  });
  await page.route("**/api/v1/maintenance/cleanup", async (route) => {
    cleanupCalls += 1;
    await route.fulfill({
      json: { days: 90, requests_removed: 2, jobs_removed: 1, graphs_removed: 3 },
    });
  });
  await page.route("**/api/v1/maintenance/restore", async (route) => {
    restoreCalls += 1;
    await route.fulfill({
      json: { name: "restore.zip", pending: true, applies_on: "next_startup" },
    });
  });

  await page.goto("/bridge/settings?tab=maintenance");
  await dismissWizard(page);
  await expect(page.getByRole("tab", { name: "数据安全" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByText(backupName, { exact: true })).toBeVisible();
  await expect(page.getByText("v0.1.0-beta.8", { exact: true })).toBeVisible();
  await expect(page.getByText("旧版（无版本状态）", { exact: true })).toBeVisible();
  await expect(page.getByText("最近启动自检", { exact: true })).toBeVisible();
  await expect(page.getByText("备份包含 Modal 凭证", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "下载" }).first()).toHaveAttribute(
    "href",
    new RegExp(backupName),
  );

  await page.getByRole("button", { name: "立即备份" }).click();
  await expect(page.getByText("备份已创建", { exact: false })).toBeVisible();
  await expect(page.getByText("ComfyBridge-backup-new.zip", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "立即按周期清理" }).click();
  await page.locator(".ant-popconfirm-buttons").getByRole("button").last().click();
  await expect(page.getByText("请求 2 条", { exact: false })).toBeVisible();
  expect(cleanupCalls).toBe(1);

  await page.locator('input[type="file"]').setInputFiles({
    name: "restore.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("zip"),
  });
  await page.getByRole("button", { name: "校验并暂存恢复" }).click();
  await page.locator(".ant-popconfirm-buttons").getByRole("button").last().click();
  await expect(page.getByText("已有备份等待恢复")).toBeVisible();
  expect(restoreCalls).toBe(1);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("设置：数据安全错误显示后端中文建议", async ({ page }) => {
  await page.route("**/api/v1/maintenance/backup", async (route) => {
    await route.fulfill({
      status: 507,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "磁盘空间不足。请清理下载目录、旧备份或回收站后重试；当前数据未被删除。",
      }),
    });
  });
  await page.goto("/bridge/settings?tab=maintenance");
  await dismissWizard(page);
  await page.getByRole("button", { name: "立即备份" }).click();
  const notice = page.locator(".ant-message-notice").filter({ hasText: "备份失败" });
  await expect(notice).toContainText("磁盘空间不足");
  await expect(notice).not.toContainText('{"detail"');
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("任务抽屉：展示当前阶段与运行耗时", async ({ page }) => {
  const now = Date.now();
  await page.route("**/api/v1/jobs?limit=50", async (route) => {
    await route.fulfill({
      json: {
        running: 1,
        jobs: [{
          id: "job-phase-1",
          kind: "node_sync",
          label: "节点同步（1 项）",
          state: "running",
          phase: "上传 node-x",
          total: 1,
          done: 0,
          items: [],
          logs: [],
          error: null,
          cancellable: true,
          created_ms: now - 12_000,
          started_ms: now - 10_000,
          finished_ms: null,
        }],
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await page.getByRole("banner").getByRole("button", { name: /任务/ }).click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("当前阶段：");
  await expect(drawer).toContainText("上传 node-x");
  await expect(drawer).toContainText("已运行");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("任务抽屉：按已完成条目估算剩余时间并说明局限", async ({ page }) => {
  const now = Date.now();
  await page.route("**/api/v1/jobs?limit=50", async (route) => {
    await route.fulfill({
      json: {
        running: 1,
        jobs: [{
          id: "job-estimate-1",
          kind: "node_sync",
          label: "节点同步（3 项）",
          state: "running",
          phase: "安装 node-b",
          total: 3,
          done: 1,
          items: [{ name: "node-a", ok: true }],
          logs: [],
          error: null,
          cancellable: true,
          created_ms: now - 11_000,
          started_ms: now - 10_000,
          finished_ms: null,
        }],
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await page.getByRole("banner").getByRole("button", { name: /任务/ }).click();
  const drawer = page.locator(".ant-drawer-open");
  const estimate = drawer.getByTestId("job-estimate");
  await expect(estimate).toContainText("预计约");
  await estimate.hover();
  await expect(page.getByRole("tooltip")).toContainText("根据本次已完成条目的平均速度估算");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("任务抽屉：无已完成条目时明确提示暂不能估算", async ({ page }) => {
  await page.route("**/api/v1/jobs?limit=50", async (route) => {
    await route.fulfill({
      json: {
        running: 1,
        jobs: [{
          id: "job-estimate-empty-1",
          kind: "node_sync",
          label: "节点同步（2 项）",
          state: "running",
          phase: "安装 node-a",
          total: 2,
          done: 0,
          items: [],
          logs: [],
          error: null,
          cancellable: true,
          created_ms: Date.now() - 1000,
          started_ms: Date.now() - 1000,
          finished_ms: null,
        }],
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await page.getByRole("banner").getByRole("button", { name: /任务/ }).click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toContainText("预计等待：样本积累中");
  await expect(drawer.getByTestId("job-estimate")).toHaveCount(0);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("节点管理：高级筛选收起、启用后保留结果", async ({ page }) => {
  await page.route("**/api/v1/nodes/diff", async (route) => {
    await route.fulfill({
      json: {
        ok: true,
        cloud_error: null,
        app_state: "running",
        volume: "comfyui-data",
        rows: [
          {
            name: "package-ready", kind: "pkg", repo: null, commit: "abc123",
            has_requirements: false, enabled: true, local: true, cloud: false,
            cloud_size: null, state: "missing_cloud",
          },
          {
            name: "package-synced", kind: "pkg", repo: null, commit: "def456",
            has_requirements: false, enabled: true, local: true, cloud: true,
            cloud_size: "1 MB", state: "same",
          },
        ],
        counts: { synced: 1, only_local: 1, only_cloud: 0 },
        cached: false,
        cached_at: null,
        class_types: { local: null, cloud: null, error: null },
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "节点管理");
  await page.getByRole("tab", { name: "差异矩阵" }).click();
  const filterButton = page.getByRole("button", { name: "高级筛选" });
  await expect(filterButton).toBeVisible();
  await filterButton.click();
  await page.getByRole("combobox", { name: "差异筛选" }).focus();
  await page.keyboard.press("Enter");
  await page.locator(".ant-select-dropdown:visible").getByText("仅云端缺失", { exact: true }).click();
  const panel = page.locator(".ant-tabs-tabpane-active");
  await expect(panel.getByText("package-ready", { exact: true })).toBeVisible();
  await expect(panel.getByText("package-synced", { exact: true })).toHaveCount(0);
  await filterButton.click();
  await expect(page.getByRole("button", { name: /高级筛选，当前条件：仅云端缺失/ })).toBeVisible();
  await expect(panel.getByText("package-ready", { exact: true })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("任务抽屉：快捷键可开关、失败项仅重跑失败子集", async ({ page }) => {
  const now = Date.now();
  let retryPayload: { names: string[] } | null = null;
  await page.route("**/api/v1/jobs?limit=50", async (route) => {
    await route.fulfill({
      json: {
        running: 0,
        jobs: [{
          id: "job-failed-1",
          kind: "node_sync",
          label: "节点同步（2 项）",
          state: "error",
          phase: "执行失败",
          total: 2,
          done: 2,
          items: [
            { name: "node-ok", ok: true, remote: "custom_nodes/node-ok" },
            { name: "node-failed", ok: false, error: "proxy connection failed" },
          ],
          logs: [],
          error: "1 个条目失败",
          cancellable: true,
          created_ms: now - 12_000,
          started_ms: now - 10_000,
          finished_ms: now,
        }],
      },
    });
  });
  await page.route("**/api/v1/nodes/sync", async (route) => {
    retryPayload = route.request().postDataJSON() as { names: string[] };
    await route.fulfill({
      json: {
        ok: true,
        job: {
          id: "job-retry-1",
          kind: "node_sync",
          label: "节点同步（1 项）",
          state: "running",
          phase: "准备中",
          total: 1,
          done: 0,
          items: [],
          logs: [],
          error: null,
          cancellable: true,
          created_ms: now,
          started_ms: null,
          finished_ms: null,
        },
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await page.keyboard.press("Control+J");
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Control+J");
  await expect(drawer).toBeHidden();
  await page.keyboard.press("Control+J");
  await expect(drawer).toContainText("重试失败项（1）");
  await drawer.getByRole("button", { name: "重试失败项（1）" }).click();
  await expect.poll(() => retryPayload).toEqual({ names: ["node-failed"] });
  await expect(drawer).toContainText("节点同步（1 项）");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("媒体库：缩略图可用键盘打开预览", async ({ page }) => {
  await page.route("**/api/v1/media/local?kind=all&limit=1000", async (route) => {
    await route.fulfill({
      json: {
        root: "C:/ComfyUI/output",
        total: 1,
        items: [{ path: "sample.png", kind: "image", size: 1024, mtime: Date.now() / 1000 }],
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "媒体库");
  const tile = page.getByRole("button", { name: "预览 sample.png" });
  await expect(tile).toBeVisible();
  await tile.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog").getByText("sample.png", { exact: true })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("设置：URL 页签可直达且错误按钮能跳转", async ({ page }) => {
  await page.goto("/bridge/settings?tab=modal");
  await dismissWizard(page);
  await expect(page.getByRole("tab", { name: "Modal", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Token ID", { exact: false }).first()).toBeVisible();
});

test("设置：向导错误可前往对应页签", async ({ page }) => {
  await page.route("**/api/v1/config", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Token not found" }),
    });
  });
  await page.goto("/bridge/");
  const dlg = page.getByRole("dialog", { name: WIZARD });
  await expect(dlg).toBeVisible();
  await dlg.getByRole("button", { name: "开始配置", exact: true }).click();
  await dlg.getByPlaceholder("ak-…").fill("ak-test");
  await dlg.getByPlaceholder("as-…").fill("as-test");
  await dlg.getByRole("button", { name: "保存并下一步" }).click();
  await dlg.getByRole("button", { name: "前往设置" }).click();
  await expect(page).toHaveURL(/\/bridge\/settings\?tab=modal$/);
  await expect(page.getByRole("tab", { name: "Modal", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("状态页与模型页：SDK 不可用时展示迁移后告警", async ({ page }) => {
  await page.route("**/api/v1/health/modal-cli", async (route) => {
    await route.fulfill({
      json: {
        found: false,
        path: "(in-process SDK)",
        version: null,
        logged_in: null,
        latency_ms: null,
        app_state: null,
        last_failure: null,
        last_success_ms: null,
        error: {
          category: "not_installed",
          label: "未安装",
          message: "modal Python SDK 不可用",
          retryable: false,
          next_action: "安装含 Python SDK 的 modal 依赖：pip install 'modal>=1.5'",
          command: null,
          exit_code: null,
        },
      },
    });
  });
  await page.route("**/api/v1/modal-volume", async (route) => {
    await route.fulfill({
      json: {
        volume: "comfyui-data",
        credentials_ok: true,
        cli_found: false,
        categories: ["checkpoints"],
      },
    });
  });

  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "系统状态");
  const statusPanel = page.locator("main");
  await expect(statusPanel.getByText("SDK 状态", { exact: true })).toBeVisible();
  await expect(statusPanel.getByText("不可用", { exact: true }).first()).toBeVisible();
  await expect(statusPanel.getByText(/Modal Python SDK 不可用：安装 `modal>=1\.5`/)).toBeVisible();

  await gotoMenu(page, "模型卷");
  await expect(page.getByText("Modal Python SDK 不可用", { exact: true })).toBeVisible();
  await expect(page.getByText(/同步通过 Modal SDK 执行/)).toBeVisible();
});

test("请求日志：表格行可用键盘打开详情并展示可行动错误", async ({ page }) => {
  const now = Date.now();
  let lastRequestUrl = "";
  await page.route("**/api/v1/requests?**", async (route) => {
    lastRequestUrl = route.request().url();
    await route.fulfill({
      json: {
        total: 1,
        items: [{
          id: 101,
          prompt_id: "prompt-keyboard-101",
          created_at: "2026-10-21T10:00:00+08:00",
          created_ms: now,
          backend: "modal",
          final_backend: "modal",
          gen_type: "image",
          workflow_name: "keyboard-flow",
          graph_hash: null,
          graph_file: null,
          client_id: null,
          status: "error",
          rule_trace: [],
          error: "proxy connection failed",
          started_ms: now,
          finished_ms: now + 1000,
          duration_ms: 1000,
          gpu_seconds: null,
          est_cost_usd: null,
        }],
      },
    });
  });
  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "日志");
  const detailButton = page.getByRole("button", { name: "查看请求详情 #101" });
  await expect(detailButton).toBeVisible();
  await detailButton.focus();
  await page.keyboard.press("Enter");
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toContainText("请求详情 #101");
  await expect(drawer).toContainText("代理连接失败");
  await expect(drawer).toContainText("关闭「使用系统代理」");
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  const filterButton = page.getByRole("button", { name: "高级筛选" });
  await filterButton.click();
  await page.getByRole("combobox", { name: "请求状态筛选" }).focus();
  await page.keyboard.press("Enter");
  await page.locator(".ant-select-dropdown:visible").getByText("失败", { exact: true }).click();
  await expect.poll(() => lastRequestUrl).toContain("status=error");
  await filterButton.click();
  await expect(page.getByRole("button", { name: /高级筛选，当前条件：失败/ })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("请求日志：大列表虚拟渲染可控且行交互不丢失", async ({ page }) => {
  const now = Date.now();
  const items = Array.from({ length: 500 }, (_, index) => ({
    id: 1000 - index,
    prompt_id: `prompt-virtual-${1000 - index}`,
    created_at: "2026-10-21T10:00:00+08:00",
    created_ms: now - index * 1000,
    backend: "modal",
    final_backend: "modal",
    gen_type: "image",
    workflow_name: `virtual-flow-${index}`,
    graph_hash: null,
    graph_file: null,
    client_id: null,
    status: "done",
    rule_trace: [],
    error: null,
    started_ms: now - index * 1000,
    finished_ms: now - index * 1000 + 900,
    duration_ms: 900,
    gpu_seconds: null,
    est_cost_usd: null,
  }));
  await page.route("**/api/v1/requests?**", async (route) => {
    await route.fulfill({ json: { total: items.length, returned: items.length, items } });
  });

  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "日志");
  await expect(page.getByText("请求日志（拦截层共 500 条）", { exact: true })).toBeVisible();
  const detailButton = page.getByRole("button", { name: "查看请求详情 #1000" });
  await expect(detailButton).toBeVisible();
  const renderedRows = page.getByRole("button", { name: /^查看请求详情 #/ });
  expect(await renderedRows.count()).toBeLessThan(100);
  await detailButton.click();
  const drawer = page.locator(".ant-drawer-open");
  await expect(drawer).toContainText("请求详情 #1000");
  await expect(drawer).toContainText("prompt-virtual-1000");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("数据卷：目录与文件条目支持键盘操作", async ({ page }) => {
  await page.route("**/api/v1/volume/meta", async (route) => {
    await route.fulfill({ json: { volume: "comfyui-data", credentials_ok: true, cli_found: true } });
  });
  await page.route("**/api/v1/volume/volumes", async (route) => {
    await route.fulfill({
      json: {
        computing: false,
        computed_ms: Date.now(),
        total_bytes: 0,
        file_count: 0,
        status: "ok",
        volumes: [],
        error: null,
      },
    });
  });
  await page.route("**/api/v1/volume/list?**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.searchParams.get("path") ?? "";
    await route.fulfill({
      json: path
        ? { ok: true, error: null, path, volume: "comfyui-data", entries: [] }
        : {
            ok: true,
            error: null,
            path: "",
            volume: "comfyui-data",
            entries: [{ name: "models", type: "dir", size: "—", mtime: "—" }],
          },
    });
  });
  await page.route("**/api/v1/volume/trash?**", async (route) => {
    await route.fulfill({ json: { ok: true, volume: "comfyui-data", entries: [], error: null } });
  });
  await page.route("**/api/v1/volume/downloads", async (route) => {
    await route.fulfill({ json: { jobs: [] } });
  });
  await page.route("**/api/v1/volume/moves", async (route) => {
    await route.fulfill({ json: { jobs: [] } });
  });

  await page.goto("/bridge/");
  await dismissWizard(page);
  await gotoMenu(page, "数据卷");
  const dirCard = page.locator(".ant-card").filter({ hasText: "目录浏览" });
  const openButton = dirCard.getByRole("button", { name: "打开", exact: true });
  await expect(openButton).toBeVisible();
  await openButton.focus();
  await page.keyboard.press("Enter");
  await expect(dirCard.getByText(/该目录为空/)).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

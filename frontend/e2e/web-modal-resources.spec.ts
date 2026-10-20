import { test, expect } from "@playwright/test";

const BASE = "http://localhost:15173";

test.describe("Modal 云端资源规格", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("设置页可配置 GPU / CPU / 内存并持久化", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("cb_wizard_done", "1");
      localStorage.setItem("cb_theme_mode", "light");
    });
    await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
    await page.getByRole("tab", { name: "Modal" }).click();

    // 用例自建前置状态：先写回默认规格，避免依赖本机历史配置（可重复执行）
    await page.request.put(`${BASE}/api/v1/config`, {
      data: { modal: { gpu: "L40S", cpu: 4, memory_mb: 32768 } },
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("tab", { name: "Modal" }).click();

    const resourcesCard = () =>
      page
        .locator(".ant-card-head-title", { hasText: "执行参数与云端资源规格" })
        .locator(
          "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' ant-card ')][1]",
        );
    const card = resourcesCard();
    await expect(card).toBeVisible();

    const gpu = card.getByRole("combobox", { name: "GPU 类型" });
    const cpu = card.getByRole("spinbutton", { name: "CPU 核心数" });
    const memory = card.getByRole("spinbutton", { name: "内存（GB）" });

    // 默认值与 runner.py 内置默认一致
    await expect(gpu).toHaveValue("L40S");
    await expect(cpu).toHaveValue("4");
    await expect(memory).toHaveValue("32");

    // 默认 L40S + 4 核 + 32 GiB 的参考价：GPU $1.95 + CPU $0.19 + 内存 $0.26
    const pricing = card.getByTestId("modal-pricing");
    await expect(pricing).toContainText("$2.39");
    await expect(pricing).toContainText("$1.95 / h");
    await expect(pricing).toContainText("L40S × 1 卡");

    await gpu.fill("H100:2");
    await cpu.fill("8");
    await memory.fill("64");
    // AntD 对两个汉字的按钮会插入字间空格，可访问名称是「保 存」
    await card.getByRole("button", { name: /保\s*存/ }).click();
    await expect(page.getByText("执行参数已保存")).toBeVisible();

    // 刷新后从后端配置回显
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("tab", { name: "Modal" }).click();
    const reloaded = resourcesCard();
    await expect(reloaded.getByRole("combobox", { name: "GPU 类型" })).toHaveValue("H100:2");
    await expect(reloaded.getByRole("spinbutton", { name: "CPU 核心数" })).toHaveValue("8");
    await expect(reloaded.getByRole("spinbutton", { name: "内存（GB）" })).toHaveValue("64");

    // 实时预估随配置变化：H100 ×2 + 8 核 + 64 GiB ≈ $8.79/h
    await expect(reloaded.getByTestId("modal-pricing")).toContainText("$8.79");
    await expect(reloaded.getByTestId("modal-pricing")).toContainText("H100 × 2 卡");

    // 部署入口存在（用例不真的触发 deploy，避免产生云端费用）
    await expect(reloaded.getByRole("button", { name: "应用并重新部署" })).toBeEnabled();
  });
});

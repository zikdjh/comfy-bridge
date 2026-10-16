import { expect, test, type Page } from "@playwright/test";

const WORKFLOW_NAME = "krea2-文生图";
const LOCAL_MODEL = "krea2_turbo_nvfp4.safetensors";
const CLOUD_MODEL = "krea2_turbo_fp8_scaled.safetensors";
const CLOUD_PROMPT = "cloud: ComfyBridge full test modal 001";
const LOCAL_PROMPT = "ComfyBridge full test local 001";
const POSITIVE_PROMPT_FALLBACK =
  "anime RPG game style, cute anime girl with gigantic fennec ears";

async function openComfy(page: Page) {
  await page.goto("/");
  await expect(page.locator("canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /^Run$/ })).toBeVisible({
    timeout: 30_000,
  });
}

async function openSavedWorkflow(page: Page, name: string) {
  await page.getByRole("button", { name: /Workflows/ }).click();
  const item = page.getByText(name, { exact: true }).first();
  await expect(item).toBeVisible({ timeout: 20_000 });
  await item.click();
  await expect(page.locator("canvas")).toBeVisible();
}

async function selectModel(page: Page, value: string) {
  const select = page.locator("select").filter({ hasText: /safetensors/i }).first();
  if (await select.count()) {
    await select.selectOption(value);
    await expect(select).toHaveValue(value);
    return;
  }

  const modelInput = page.locator("input").filter({ hasValue: /\.safetensors$/ }).first();
  await expect(modelInput).toBeVisible({ timeout: 20_000 });
  await modelInput.fill(value);
  await expect(modelInput).toHaveValue(value);
}

async function setFirstPrompt(page: Page, value: string) {
  const textarea = page.locator("textarea").first();
  await expect(textarea).toBeVisible({ timeout: 20_000 });
  await textarea.fill(value);
  await expect(textarea).toHaveValue(value);
}

async function queueAndWait(page: Page, timeoutMs: number) {
  const run = page.getByRole("button", { name: /^Run$/ });
  await expect(run).toBeVisible();
  await run.click();
  const cancel = page.getByRole("button", { name: /Cancel current run/ });
  await expect(cancel).toBeEnabled({ timeout: 20_000 });
  await expect(cancel).toBeDisabled({ timeout: timeoutMs });
}

test("WF-01 加载标准工作流并检查关键节点", async ({ page }) => {
  await openComfy(page);
  await openSavedWorkflow(page, WORKFLOW_NAME);
  await expect(page.getByText(POSITIVE_PROMPT_FALLBACK, { exact: false })).toBeVisible({
    timeout: 30_000,
  });
  await page.screenshot({ path: "test-results/fulltest/WF-01-workflow-loaded.png", fullPage: true });
});

test("GEN-01 本地文生图：切换 4-bit 模型并点击 Run", async ({ page }) => {
  test.setTimeout(300_000);
  await openComfy(page);
  await openSavedWorkflow(page, WORKFLOW_NAME);
  await selectModel(page, LOCAL_MODEL);
  await setFirstPrompt(page, LOCAL_PROMPT);
  await page.screenshot({ path: "test-results/fulltest/GEN-01-local-ready.png", fullPage: true });
  await queueAndWait(page, 240_000);
  await page.screenshot({ path: "test-results/fulltest/GEN-01-local-done.png", fullPage: true });
});

test("GEN-04 云端文生图：切换 fp8 模型并点击 Run", async ({ page }) => {
  test.setTimeout(420_000);
  await openComfy(page);
  await openSavedWorkflow(page, WORKFLOW_NAME);
  await selectModel(page, CLOUD_MODEL);
  await setFirstPrompt(page, CLOUD_PROMPT);
  await page.screenshot({ path: "test-results/fulltest/GEN-04-cloud-ready.png", fullPage: true });
  await queueAndWait(page, 360_000);
  await page.screenshot({ path: "test-results/fulltest/GEN-04-cloud-done.png", fullPage: true });
});

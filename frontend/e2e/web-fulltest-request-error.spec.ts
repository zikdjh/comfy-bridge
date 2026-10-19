import { expect, test } from "@playwright/test";

test("request log exposes API failures and can retry", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
  let failRequests = true;
  await page.route("**/api/v1/requests?**", async (route) => {
    if (failRequests) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "fulltest simulated request-list failure" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("http://localhost:15173/logs", { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "请求日志" }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("请求日志读取失败");
  await expect(alert).toContainText("500");
  const alertHtml = await page.locator(".ant-alert").first().evaluate((node) => node.outerHTML);
  await test.info().attach("request-error-alert", { body: alertHtml, contentType: "text/html" });

  failRequests = false;
  const retry = page.locator(".ant-alert-action button");
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.locator(".ant-table-row").first()).toBeVisible({ timeout: 20_000 });
});

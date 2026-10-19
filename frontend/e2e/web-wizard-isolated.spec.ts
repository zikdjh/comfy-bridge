import { expect, test } from "@playwright/test";

test("isolated configuration wizard focus and Escape behavior", async ({ page }) => {
  await page.route("http://localhost:15173/api/**", async (route) => {
    if (route.request().url().includes("/events/stream")) {
      await route.abort();
      return;
    }
    const url = new URL(route.request().url());
    url.protocol = "http:";
    url.host = "127.0.0.1:18389";
    const response = await route.fetch({ url: url.toString() });
    await route.fulfill({ response });
  });
  await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
  await page.goto("http://localhost:15173/settings", { waitUntil: "networkidle" });
  await expect(page.getByRole("button", { name: "配置向导", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "配置向导", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const focus = await page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    return {
      tag: active?.tagName ?? null,
      inDialog: Boolean(active?.closest("[role='dialog']")),
      text: active?.textContent?.trim().slice(0, 80) ?? "",
    };
  });
  expect(focus.inDialog).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("account profile modal focuses input and closes with Escape", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
  await page.goto("http://localhost:15173/settings", { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "Modal", exact: true }).click();
  await page.getByRole("button", { name: /保存当前为档案/ }).click();
  const dialog = page.getByRole("dialog", { name: "保存当前配置为账号档案" });
  await expect(dialog).toBeVisible();
  const input = dialog.getByPlaceholder("档案名称，如 personal / team");
  await expect(input).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

for (const width of [768, 1024]) {
  test(`settings and request logs fit viewport width at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("http://localhost:15173/api/**", async (route) => {
      if (route.request().url().includes("/events/stream")) {
        await route.abort();
        return;
      }
      const url = new URL(route.request().url());
      url.protocol = "http:";
      url.host = "127.0.0.1:18389";
      const response = await route.fetch({ url: url.toString() });
      await route.fulfill({ response });
    });
    await page.addInitScript(() => localStorage.setItem("cb_wizard_done", "1"));
    for (const route of ["settings", "logs"]) {
      await page.goto(`http://localhost:15173/${route}`, { waitUntil: "networkidle" });
      const dimensions = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
      console.log(`RESPONSIVE_${width}_${route}=` + JSON.stringify(dimensions));
      expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    }
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
}

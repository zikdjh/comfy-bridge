import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const BASE = "http://localhost:15173";
const EVIDENCE_DIR =
  "C:/Users/z2351/.codex/visualizations/2026/10/15/01a13d51-a08d-7370-85c3-888b64255c42/ego/comfy-bridge-web-20261018/round5";

test.describe.configure({ mode: "serial" });

test("configuration wizard closes with Escape and retains dialog focus", async ({ page }) => {
  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await expect(page.getByText("ComfyUI 根目录", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "配置向导", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("ComfyBridge 首次配置向导", { exact: true })).toBeVisible();
  await expect(dialog).toContainText("Modal 云端凭证");
  await expect(dialog).toContainText("本地 ComfyUI 根目录");
  await expect(dialog).toContainText("云端 Runner 通道");
  await page.screenshot({ path: `${EVIDENCE_DIR}/wizard-open.png` });

  const visibleFocus = await page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    return {
      inDialog: Boolean(active?.closest("[role='dialog']")),
      tag: active?.tagName ?? null,
      text: active?.textContent?.trim().slice(0, 80) ?? "",
    };
  });
  expect(visibleFocus.inDialog, `initial modal focus escaped: ${visibleFocus.tag} ${visibleFocus.text}`).toBe(true);

  for (let i = 0; i < 5; i += 1) {
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      return {
        inDialog: Boolean(active?.closest("[role='dialog']")),
        tag: active?.tagName ?? null,
        text: active?.textContent?.trim().slice(0, 80) ?? "",
      };
    });
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "配置向导", exact: true })).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/wizard-closed.png` });
});

test("configuration wizard Escape closes even when initial focus is outside", async ({ page }) => {
  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "配置向导", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const focusText = await page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    return `${active?.tagName ?? "none"}:${active?.textContent?.trim().slice(0, 80) ?? ""}`;
  });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await test.info().attach("focus-before-escape", { body: focusText, contentType: "text/plain" });
});

test("responsive measurements are captured even when overflow assertion fails", async ({ page }) => {
  const results: Array<{
    width: number;
    route: string;
    viewport: number;
    scrollWidth: number;
    mainWidth: number;
    mainText: string;
    offenders: Array<{ tag: string; cls: string; left: number; right: number; width: number; text: string }>;
  }> = [];
  for (const width of [1366, 1024, 768]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ["/settings", "/logs", "/cost", "/volume"]) {
      await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
      await expect(page.locator("main")).toBeVisible();
      await page.waitForTimeout(1200);
      const result = await page.evaluate((currentRoute) => {
        const main = document.querySelector("main") as HTMLElement | null;
        const mainRect = main?.getBoundingClientRect();
        const offenders = [...document.querySelectorAll<HTMLElement>("body *")]
          .filter((node) => {
            if (node.closest(".ant-table-content, .ant-table-body, .ant-table-container")) return false;
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && rect.right > window.innerWidth + 1;
          })
          .slice(0, 20)
          .map((node) => {
            const rect = node.getBoundingClientRect();
            return {
              tag: node.tagName.toLowerCase(),
              cls: typeof node.className === "string" ? node.className.slice(0, 160) : "",
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              width: Math.round(rect.width),
              text: node.textContent?.trim().replace(/\s+/g, " ").slice(0, 100) ?? "",
            };
          });
        return {
          width: window.innerWidth,
          route: currentRoute,
          viewport: window.innerWidth,
          scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
          mainWidth: Math.round(mainRect?.width ?? 0),
          mainText: main?.innerText.trim().slice(0, 200) ?? "",
          offenders,
        };
      }, route);
      results.push({ width, ...result });
      await page.screenshot({
        path: `${EVIDENCE_DIR}/responsive-${width}-${route.slice(1)}.png`,
        fullPage: true,
      });
    }
  }
  await page.evaluate((payload) => {
    (window as typeof window & { __COMFYBRIDGE_RESPONSIVE_RESULTS__?: unknown }).__COMFYBRIDGE_RESPONSIVE_RESULTS__ =
      payload;
  }, results);
  await writeFile(`${EVIDENCE_DIR}/responsive-measurements.json`, JSON.stringify(results, null, 2), "utf8");
  expect(results).toHaveLength(12);
  for (const result of results) {
    expect(result.mainWidth, `${result.width}${result.route} main missing`).toBeGreaterThan(200);
    expect(result.mainText.length, `${result.width}${result.route} main blank`).toBeGreaterThan(10);
  }
});

test("diagnose account profile controls without mutating config", async ({ page }) => {
  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "Modal", exact: true }).click();
  await expect(page.getByText("账号档案", { exact: true })).toBeVisible();
  const state = await page.evaluate(() => {
    const controls = [...document.querySelectorAll<HTMLElement>("button, input, [role='button'], [role='tab']")]
      .filter((node) => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      })
      .map((node) => ({
        tag: node.tagName.toLowerCase(),
        role: node.getAttribute("role"),
        text: node.textContent?.trim().replace(/\s+/g, " ").slice(0, 120) ?? "",
        aria: node.getAttribute("aria-label"),
        placeholder: node.getAttribute("placeholder"),
        disabled: node.hasAttribute("disabled"),
      }));
    return { controls, text: document.querySelector("main")?.innerText.slice(-1500) ?? "" };
  });
  await page.screenshot({ path: `${EVIDENCE_DIR}/account-controls.png`, fullPage: true });
  await writeFile(`${EVIDENCE_DIR}/account-controls.json`, JSON.stringify(state, null, 2), "utf8");
  console.log(JSON.stringify(state.controls.filter((item) => /档案|保存|Modal|移除/.test(item.text)), null, 2));
});

test("diagnose account save dialog DOM without mutation", async ({ page }) => {
  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "Modal", exact: true }).click();
  await page.getByRole("button", { name: "保存当前为档案", exact: true }).click();
  await page.waitForTimeout(1000);
  const state = await page.evaluate(() => ({
    dialogs: [...document.querySelectorAll<HTMLElement>(".ant-modal, [role='dialog']")].map((node) => ({
      tag: node.tagName.toLowerCase(),
      role: node.getAttribute("role"),
      visible: node.offsetParent !== null,
      text: node.innerText.slice(0, 500),
      html: node.outerHTML.slice(0, 1000),
    })),
    bodyText: document.body.innerText.slice(-1200),
  }));
  await page.screenshot({ path: `${EVIDENCE_DIR}/account-dialog-diagnostic.png`, fullPage: true });
  await writeFile(`${EVIDENCE_DIR}/account-dialog-diagnostic.json`, JSON.stringify(state, null, 2), "utf8");
  console.log(JSON.stringify(state.dialogs, null, 2));
});

test("responsive layouts expose content and do not overflow", async ({ page }) => {
  const results: Array<{
    width: number;
    route: string;
    viewport: number;
    scrollWidth: number;
    mainWidth: number;
    mainText: string;
    offenders: Array<{ tag: string; cls: string; left: number; right: number; width: number; text: string }>;
  }> = [];

  for (const width of [1366, 1024, 768]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ["/settings", "/logs", "/cost", "/volume"]) {
      await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
      await expect(page.locator("main")).toBeVisible();
      await expect(page.locator("main")).not.toBeEmpty();
      await page.waitForTimeout(700);
      const result = await page.evaluate((currentRoute) => {
        const main = document.querySelector("main") as HTMLElement | null;
        const mainRect = main?.getBoundingClientRect();
        const offenders = [...document.querySelectorAll<HTMLElement>("body *")]
          .filter((node) => {
            if (node.closest(".ant-table-content, .ant-table-body, .ant-table-container")) return false;
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && rect.right > window.innerWidth + 1;
          })
          .slice(0, 20)
          .map((node) => {
            const rect = node.getBoundingClientRect();
            return {
              tag: node.tagName.toLowerCase(),
              cls: typeof node.className === "string" ? node.className.slice(0, 160) : "",
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              width: Math.round(rect.width),
              text: node.textContent?.trim().replace(/\s+/g, " ").slice(0, 100) ?? "",
            };
          });
        return {
          route: currentRoute,
          viewport: window.innerWidth,
          scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
          mainWidth: Math.round(mainRect?.width ?? 0),
          mainText: main?.innerText.trim().slice(0, 200) ?? "",
          offenders,
        };
      }, route);
      results.push({ width, ...result });
      if (width === 768 && ["/settings", "/logs"].includes(route)) {
        await page.screenshot({ path: `${EVIDENCE_DIR}/responsive-${width}${route.replace("/", "-")}.png`, fullPage: true });
      }
    }
  }

  await test.info().attach("responsive-results", {
    body: JSON.stringify(results, null, 2),
    contentType: "application/json",
  });

  for (const result of results) {
    expect(result.mainWidth, `${result.width}${result.route} main missing`).toBeGreaterThan(200);
    expect(result.mainText.length, `${result.width}${result.route} main blank`).toBeGreaterThan(10);
    expect(result.scrollWidth, `${result.width}${result.route} horizontal overflow`).toBeLessThanOrEqual(
      result.viewport + 8,
    );
  }
});

test("account profile card can save current snapshot and delete inactive profile", async ({ page, request }) => {
  const uniqueName = `fulltest-ui-r5-${Date.now()}`;
  const accountsBefore = await request.get("http://127.0.0.1:18189/api/v1/config/accounts");
  expect(accountsBefore.ok()).toBeTruthy();
  const beforeBody = (await accountsBefore.json()) as { accounts: Array<{ name: string }>; active: string | null };
  expect(beforeBody.accounts).not.toHaveLength(1);

  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "Modal", exact: true }).click();
  await expect(page.getByText("账号档案", { exact: true })).toBeVisible();
  console.log("ACCOUNT_TEST: open save dialog");
  await page.getByRole("button", { name: "保存当前为档案", exact: true }).click();
  const saveDialog = page.getByRole("dialog");
  await expect(saveDialog).toBeVisible();
  console.log("ACCOUNT_TEST: dialog visible");
  await expect(saveDialog.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
  await saveDialog.getByPlaceholder("档案名称，如 personal / team").fill(uniqueName);
  await expect(saveDialog.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
  console.log("ACCOUNT_TEST: submit save");
  await saveDialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText(uniqueName, { exact: true })).toBeVisible();
  console.log("ACCOUNT_TEST: row visible");
  await expect(page.getByText("激活中", { exact: true })).toBeVisible();

  const accountsAfterSave = await request.get("http://127.0.0.1:18189/api/v1/config/accounts");
  const savedBody = (await accountsAfterSave.json()) as { accounts: Array<{ name: string; active: boolean }>; active: string };
  expect(savedBody.active).toBe(uniqueName);
  expect(savedBody.accounts).toEqual(
    expect.arrayContaining([expect.objectContaining({ name: uniqueName, active: true })]),
  );

  const deleteResponse = await request.delete(
    `http://127.0.0.1:18189/api/v1/config/accounts/${encodeURIComponent(uniqueName)}`,
  );
  expect(deleteResponse.status()).toBe(409);
  await page.screenshot({ path: `${EVIDENCE_DIR}/account-profile-active.png`, fullPage: true });
});

test("account profile modal Escape returns focus to trigger", async ({ page }) => {
  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "Modal", exact: true }).click();
  const trigger = page.getByRole("button", { name: "保存当前为档案", exact: true });
  console.log("ACCOUNT_TEST: open escape dialog");
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  console.log("ACCOUNT_TEST: escape dialog visible");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const OUT = "E:/JAVA/work/web/zc/modal/comfy-bridge/tmp/ui-verify";

test("cloud overlay visible during cloud run", { timeout: 300000 }, async ({ page }) => {
  await page.goto("http://127.0.0.1:8189/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(6000);

  // 悬浮组件初始隐藏（无在途任务）
  const initial = await page.evaluate(() => {
    const p = document.getElementById("cb-cloud-overlay");
    return p ? { exists: true, visible: p.style.display !== "none" } : { exists: false };
  });
  console.log("initial overlay:", JSON.stringify(initial));
  await page.screenshot({ path: `${OUT}/v1-no-task.png` });

  const clientId = await page.evaluate(() => {
    const app = (window as unknown as { app?: { api?: { clientId?: string } } }).app;
    return app?.api?.clientId ?? null;
  });
  console.log("clientId:", clientId);

  const fixture = JSON.parse(readFileSync("E:/JAVA/work/web/zc/modal/comfy-bridge/tmp/live-fixtures/cloud-txt2img.json", "utf-8"));
  const submit = await page.evaluate(async ({ prompt, clientId }) => {
    const r = await fetch("/prompt", {
      method: "POST",
      headers: { "content-type": "application/json", "x-bridge-privacy-confirmed": "true" },
      body: JSON.stringify({ prompt, client_id: clientId }),
    });
    return { status: r.status, body: await r.json() };
  }, { prompt: fixture.prompt, clientId });
  console.log("submit:", JSON.stringify(submit).slice(0, 120));
  expect(submit.status).toBe(200);

  // 等悬浮组件轮询周期（3s）+ 缓冲
  await page.waitForTimeout(6000);
  const during = await page.evaluate(() => {
    const p = document.getElementById("cb-cloud-overlay");
    if (!p) return { exists: false };
    return {
      exists: true,
      visible: p.style.display !== "none",
      text: p.innerText.replace(/\n/g, " | ").slice(0, 150),
    };
  });
  console.log("during overlay:", JSON.stringify(during));
  await page.screenshot({ path: `${OUT}/v2-overlay-running.png` });
  expect(during.visible).toBe(true);

  // 等任务完成
  for (let i = 0; i < 8; i++) {
    await page.waitForTimeout(25000);
    const q = await page.evaluate(async () => (await (await fetch("/queue")).json()));
    console.log(`poll ${i}: running=${q.queue_running.length}`);
    if (q.queue_running.length === 0) {
      const after = await page.evaluate(() => {
        const p = document.getElementById("cb-cloud-overlay");
        return p ? { visible: p.style.display !== "none" } : { exists: false };
      });
      console.log("after overlay:", JSON.stringify(after));
      await page.screenshot({ path: `${OUT}/v3-overlay-done.png` });
      expect(after.visible).toBe(false);
      break;
    }
  }
});

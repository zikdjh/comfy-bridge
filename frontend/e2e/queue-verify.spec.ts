import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const OUT = "E:/JAVA/work/web/zc/modal/comfy-bridge/tmp/ui-verify";

test("overlay shows progress bar", { timeout: 300000 }, async ({ page }) => {
  await page.goto("http://127.0.0.1:18189/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(6000);

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

  // 轮询悬浮组件文本，等出现 progress 条（最长 90s）
  let sawProgress = false;
  for (let i = 0; i < 18; i++) {
    await page.waitForTimeout(5000);
    const info = await page.evaluate(() => {
      const p = document.getElementById("cb-cloud-overlay");
      if (!p) return { exists: false };
      return { exists: true, visible: p.style.display !== "none", text: p.innerText.replace(/\n/g, " | ") };
    });
    console.log(`t+${(i+1)*5}s:`, JSON.stringify(info).slice(0, 250));
    if (info.exists && info.text && info.text.includes("%")) {
      sawProgress = true;
      await page.screenshot({ path: `${OUT}/w2-overlay-progress.png` });
      break;
    }
  }
  expect(sawProgress).toBe(true);

  // 等任务结束，确认悬浮隐藏
  for (let i = 0; i < 8; i++) {
    await page.waitForTimeout(25000);
    const q = await page.evaluate(async () => (await (await fetch("/queue")).json()));
    console.log(`poll ${i}: running=${q.queue_running.length}`);
    if (q.queue_running.length === 0) {
      const after = await page.evaluate(() => {
        const p = document.getElementById("cb-cloud-overlay");
        return p ? p.style.display !== "none" : false;
      });
      console.log("after hidden:", !after);
      await page.screenshot({ path: `${OUT}/w3-done.png` });
      expect(after).toBe(false);
      break;
    }
  }
});

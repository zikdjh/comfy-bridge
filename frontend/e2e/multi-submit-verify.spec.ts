import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const OUT = "E:/JAVA/work/web/zc/modal/comfy-bridge/tmp/ui-verify";

const PROMPTS = [
  {
    label: "neon-city",
    text: "a neon-lit cyberpunk city at night, rain-slick streets, reflections, cinematic, highly detailed",
  },
  {
    label: "mountain-dawn",
    text: "misty mountain range at dawn, golden light over peaks, wide landscape, serene",
  },
  {
    label: "still-life",
    text: "a still life with ceramic vase, wildflowers, soft window light, muted palette, oil painting style",
  },
];

test("submit multiple prompts and capture overlay", { timeout: 600000 }, async ({ page }) => {
  await page.goto("http://127.0.0.1:8189/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(6000);

  const clientId = await page.evaluate(() => {
    const app = (window as unknown as { app?: { api?: { clientId?: string } } }).app;
    return app?.api?.clientId ?? null;
  });
  console.log("clientId:", clientId);
  expect(clientId).toBeTruthy();

  const fixture = JSON.parse(
    readFileSync("E:/JAVA/work/web/zc/modal/comfy-bridge/tmp/live-fixtures/cloud-txt2img.json", "utf-8")
  );

  // patch prompt text for each submission while keeping graph shape
  const results: { label: string; status: number; prompt_id?: string }[] = [];
  for (const p of PROMPTS) {
    const modified = JSON.parse(JSON.stringify(fixture));
    // find the KSampler positive prompt text node (CLIPTextEncode) and replace its text
    for (const [id, node] of Object.entries<Record<string, unknown>>(modified.prompt)) {
      if (node.class_type === "CLIPTextEncode" && typeof (node.inputs as Record<string, unknown>)?.text === "string") {
        (node.inputs as Record<string, unknown>).text = p.text;
        break;
      }
    }
    const submit = await page.evaluate(async ({ prompt, clientId }) => {
      const r = await fetch("/prompt", {
        method: "POST",
        headers: { "content-type": "application/json", "x-bridge-privacy-confirmed": "true" },
        body: JSON.stringify({ prompt, client_id: clientId }),
      });
      return { status: r.status, body: await r.json() };
    }, { prompt: modified.prompt, clientId });
    console.log(`submit ${p.label}:`, JSON.stringify(submit).slice(0, 120));
    results.push({ label: p.label, status: submit.status, prompt_id: submit.body?.prompt_id });
    expect(submit.status).toBe(200);
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${OUT}/multi-${p.label}-submitted.png` });
  }

  // poll overlay for progress across all tasks
  let sawAnyProgress = false;
  for (let i = 0; i < 36; i++) {
    await page.waitForTimeout(5000);
    const info = await page.evaluate(() => {
      const p = document.getElementById("cb-cloud-overlay");
      if (!p) return { exists: false };
      return { exists: true, visible: p.style.display !== "none", text: p.innerText.replace(/\n/g, " | ") };
    });
    console.log(`t+${(i + 1) * 5}s:`, JSON.stringify(info).slice(0, 300));
    if (info.exists && info.text && info.text.includes("%")) {
      if (!sawAnyProgress) {
        sawAnyProgress = true;
        await page.screenshot({ path: `${OUT}/multi-first-progress.png` });
      }
      await page.screenshot({ path: `${OUT}/multi-progress-latest.png` });
    }
    const q = await page.evaluate(async () => await (await fetch("/queue")).json());
    if (q.queue_running.length === 0 && q.queue_pending.length === 0 && i > 2) {
      console.log("all tasks complete");
      break;
    }
  }

  expect(sawAnyProgress).toBe(true);
  await page.screenshot({ path: `${OUT}/multi-final.png` });
});

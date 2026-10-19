const evidence =
  "C:/Users/z2351/.codex/visualizations/2026/10/15/01a13d51-a08d-7370-85c3-888b64255c42/ego/comfy-bridge-web-20261018";
const task = await taskSpace(1);
const page = task.page("p1");
const shots = [];
const results = [];
const common = { task, page, state, clickText, shot, results };

async function shot(name) {
  const path = `${evidence}/${name}.png`;
  await page.screenshot({ path });
  shots.push(path);
}

async function state(name, selector = null) {
  const value = await page.evaluate((sel) => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none"
      );
    };
    return {
      url: location.href,
      text: document.body.innerText.slice(0, 14000),
      tabs: [...document.querySelectorAll("[role=tab]")].map((el) => ({
        text: el.innerText.trim(),
        selected: el.getAttribute("aria-selected"),
      })),
      segmented: [...document.querySelectorAll(".ant-segmented-item")]
        .filter(visible)
        .map((el) => ({
          text: el.innerText.trim(),
          selected: el.classList.contains("ant-segmented-item-selected"),
        })),
      buttons: [...document.querySelectorAll("button")]
        .filter(visible)
        .map((el) => ({
          text: el.innerText.trim(),
          aria: el.getAttribute("aria-label"),
          title: el.getAttribute("title"),
        })),
      tables: [...document.querySelectorAll("table")].map((el) =>
        el.innerText.slice(0, 9000),
      ),
      alerts: [...document.querySelectorAll(".ant-alert")]
        .filter(visible)
        .map((el) => el.innerText.slice(0, 3000)),
      images: [...document.querySelectorAll("img")]
        .filter((el) => el.getBoundingClientRect().width > 0)
        .slice(0, 20)
        .map((el) => ({
          src: el.src.slice(0, 500),
          alt: el.alt,
          naturalWidth: el.naturalWidth,
          naturalHeight: el.naturalHeight,
        })),
      target: sel
        ? [...document.querySelectorAll(sel)]
            .filter(visible)
            .map((el) => el.innerText.slice(0, 5000))
        : null,
    };
  }, selector);
  results.push({ name, state: value });
  return value;
}

async function clickText(text, label) {
  const position = await page.evaluate((value) => {
    const nodes = [
      ...document.querySelectorAll(
        "button,a,[role=tab],.ant-segmented-item,.ant-tabs-tab",
      ),
    ];
    const el = nodes.find(
      (node) =>
        node.innerText.trim() === value &&
        node.getBoundingClientRect().width > 0,
    );
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }, text);
  if (!position) throw new Error(`target not found: ${text}`);
  await page.mouse.click(position.x, position.y, { label });
  await page.waitForTimeout(750);
}

try {
  await page.goto("http://localhost:15173/workflows", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1300);
  for (const tab of ["本地清单", "同步状态", "运行统计"]) {
    await clickText(tab, `切换工作流${tab}`);
    await page.waitForTimeout(1100);
    await state(`workflows-${tab}`);
  }
  const dayFilters = await page.evaluate(() =>
    [...document.querySelectorAll(".ant-segmented-item")]
      .filter((el) =>
        ["近 7 天", "近 30 天", "近 90 天"].includes(el.innerText.trim()),
      )
      .map((el) => el.innerText.trim()),
  );
  for (const label of dayFilters) {
    await clickText(label, `切换运行统计${label}`);
    await page.waitForTimeout(900);
    await state(`workflows-run-${label}`);
  }
  await shot("ui-followup2-workflows");

  await page.goto("http://localhost:15173/media", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1300);
  const mediaOptions = await page.evaluate(() =>
    [...document.querySelectorAll('input[type="radio"]')].map((el) => ({
      value: el.value,
      checked: el.checked,
      label: el.labels?.[0]?.innerText?.trim() ?? "",
    })),
  );
  results.push({ name: "media-radio-options", mediaOptions });
  for (const option of mediaOptions.filter((item) =>
    ["image", "video"].includes(item.value),
  )) {
    await page.evaluate((value) => {
      const el = [...document.querySelectorAll('input[type="radio"]')].find(
        (node) => node.value === value,
      );
      el?.click();
    }, option.value);
    await page.waitForTimeout(900);
    await state(`media-${option.value}`);
  }
  const media = await page.evaluate(() => {
    const el = [...document.querySelectorAll("img")].find(
      (node) =>
        node.naturalWidth > 0 &&
        node.getBoundingClientRect().width > 0,
    );
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  if (media) {
    await page.mouse.click(media.x, media.y, { label: "打开媒体详情" });
    await page.waitForTimeout(900);
    await state("media-detail", ".ant-modal");
    await shot("ui-followup2-media-detail");
    await page.keyboard.press("Escape");
  } else {
    results.push({ name: "media-detail", error: "no-visible-image" });
  }
  await shot("ui-followup2-media");

  await page.goto("http://localhost:15173/cost", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1300);
  await state("cost-default");
  for (const tab of ["按工作流", "按主模型"]) {
    await clickText(tab, `切换成本${tab}`);
    await page.waitForTimeout(900);
    await state(`cost-${tab}`);
  }
  await shot("ui-followup2-cost");

  await page.goto("http://localhost:15173/logs", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1200);
  await state("logs-requests");
  await clickText("实时控制台", "打开实时控制台");
  await page.waitForTimeout(1200);
  await state("logs-console");
  await shot("ui-followup2-logs");

  await page.goto("http://localhost:15173/status", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1200);
  await state("status");
  const selfCheck = await page.evaluate(() => {
    const el = [...document.querySelectorAll("button")].find((node) =>
      node.innerText.includes("重新自检"),
    );
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  if (selfCheck) {
    await page.mouse.click(selfCheck.x, selfCheck.y, {
      label: "重新执行系统自检",
    });
    await page.waitForTimeout(2000);
    await state("status-after-selfcheck");
  }
  await shot("ui-followup2-status");

  await page.goto("http://localhost:15173/settings", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1400);
  for (const tab of [
    "本地",
    "启动命令",
    "桥接",
    "Modal",
    "护栏与预算",
    "设置同步",
  ]) {
    await clickText(tab, `切换设置${tab}`);
    await page.waitForTimeout(tab === "设置同步" ? 1300 : 750);
    await state(`settings-${tab}`);
  }
  await shot("ui-followup2-settings");
} catch (error) {
  results.push({
    name: "fatal",
    fatal: String(error),
    stack: error?.stack ?? null,
  });
  try {
    await shot("ui-followup2-fatal");
  } catch {}
} finally {
  const fs = await import("node:fs/promises");
  const path = `${evidence}/ui-followup-round2-results.json`;
  await fs.writeFile(
    path,
    JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        scope: "ego-browser UI click round2; no prompt submission",
        shots,
        results,
      },
      null,
      2,
    ),
    "utf8",
  );
  console.log(
    JSON.stringify(
      {
        path,
        shotCount: shots.length,
        resultCount: results.length,
        fatal: results.find((item) => item.fatal)?.fatal ?? null,
      },
      null,
      2,
    ),
  );
}

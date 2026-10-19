const evidence =
  "C:/Users/z2351/.codex/visualizations/2026/10/15/01a13d51-a08d-7370-85c3-888b64255c42/ego/comfy-bridge-web-20261018";
const task = await taskSpace(1);
const page = task.page("p1");
const shots = [];
const results = [];

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
      title: document.title,
      text: document.body.innerText.slice(0, 16000),
      tabs: [...document.querySelectorAll("[role=tab]")].map((el) => ({
        text: el.innerText.trim(),
        selected: el.getAttribute("aria-selected"),
      })),
      buttons: [...document.querySelectorAll("button")]
        .filter(visible)
        .map((el) => ({
          text: el.innerText.trim(),
          aria: el.getAttribute("aria-label"),
          title: el.getAttribute("title"),
          disabled: el.disabled,
        })),
      tables: [...document.querySelectorAll("table")].map((el) =>
        el.innerText.slice(0, 9000),
      ),
      alerts: [...document.querySelectorAll(".ant-alert")]
        .filter(visible)
        .map((el) => el.innerText.slice(0, 3500)),
      inputs: [...document.querySelectorAll("input,textarea")]
        .filter(visible)
        .slice(0, 50)
        .map((el) => ({
          tag: el.tagName,
          type: el.type,
          value: el.value,
          placeholder: el.placeholder,
          disabled: el.disabled,
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
  await page.goto("http://localhost:15173/status", {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.waitForTimeout(1500);
  await state("status");
  const selfCheck = await page.evaluate(() => {
    const el = [...document.querySelectorAll("button")].find((node) =>
      node.innerText.includes("重新自检"),
    );
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      disabled: el.disabled,
    };
  });
  if (selfCheck) {
    await page.mouse.click(selfCheck.x, selfCheck.y, {
      label: "重新执行系统自检",
    });
    await page.waitForTimeout(2500);
    await state("status-after-selfcheck");
  } else {
    results.push({ name: "status-selfcheck", error: "button-not-found" });
  }
  await shot("ui-followup3-status");

  await page.goto("http://localhost:15173/settings", {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.waitForTimeout(1600);
  for (const tab of [
    "本地",
    "启动命令",
    "桥接",
    "Modal",
    "护栏与预算",
    "设置同步",
  ]) {
    await clickText(tab, `切换设置${tab}`);
    await page.waitForTimeout(tab === "设置同步" ? 1400 : 800);
    await state(`settings-${tab}`);
  }
  await shot("ui-followup3-settings");

  await page.goto("http://localhost:15173/routing", {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.waitForTimeout(1400);
  await state("routing");
  await shot("ui-followup3-routing");
} catch (error) {
  results.push({
    name: "fatal",
    fatal: String(error),
    stack: error?.stack ?? null,
  });
  try {
    await shot("ui-followup3-fatal");
  } catch {}
} finally {
  const fs = await import("node:fs/promises");
  const path = `${evidence}/ui-followup-round3-results.json`;
  await fs.writeFile(
    path,
    JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        scope: "ego-browser UI click round3; no prompt submission",
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

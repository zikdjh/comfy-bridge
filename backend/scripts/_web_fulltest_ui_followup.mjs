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
  return path;
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
      text: document.body.innerText.slice(0, 12000),
      headings: [...document.querySelectorAll("h1,h2,h3")]
        .map((el) => el.innerText.trim())
        .filter(Boolean),
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
        el.innerText.slice(0, 7000),
      ),
      alerts: [...document.querySelectorAll(".ant-alert")]
        .filter(visible)
        .map((el) => el.innerText.slice(0, 2500)),
      inputs: [...document.querySelectorAll("input,textarea")]
        .filter(visible)
        .slice(0, 40)
        .map((el) => ({
          tag: el.tagName,
          type: el.type,
          value: el.value,
          placeholder: el.placeholder,
        })),
      selects: [...document.querySelectorAll(".ant-select")]
        .filter(visible)
        .map((el) => el.innerText.trim()),
      target: sel
        ? [...document.querySelectorAll(sel)]
            .filter(visible)
            .map((el) => el.innerText.slice(0, 4000))
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
        "button,a,[role=menuitem],[role=tab],.ant-segmented-item,.ant-tabs-tab",
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
  await page.waitForTimeout(700);
}

try {
  await page.goto("http://localhost:15173/", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1800);
  await state("dashboard");
  await shot("ui-followup-dashboard");

  const navigation = [
    "路由规则",
    "节点管理",
    "工作流",
    "模型卷",
    "数据卷",
    "媒体库",
    "成本报表",
    "日志",
    "系统状态",
    "设置",
  ];
  for (const name of navigation) {
    const before = await page.url();
    await clickText(name, `导航到${name}`);
    await page.waitForTimeout(900);
    const value = await state(`nav-${name}`);
    results.push({
      name: `nav-assert-${name}`,
      before,
      after: value.url,
      ok: before !== value.url,
    });
  }

  await page.goto("http://localhost:15173/", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1000);
  const drawer = await page.evaluate(() => {
    const el = [...document.querySelectorAll("button")].find(
      (node) => node.innerText.trim() === "任务",
    );
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  if (drawer) {
    await page.mouse.click(drawer.x, drawer.y, { label: "打开任务抽屉" });
    await page.waitForTimeout(800);
    await state("task-drawer-open", ".ant-drawer");
    await shot("ui-followup-task-drawer");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    await state("task-drawer-closed", ".ant-drawer");
  } else {
    results.push({ name: "task-drawer", error: "button-not-found" });
  }

  await page.goto("http://localhost:15173/nodes", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1500);
  for (const tab of ["本地清单", "差异矩阵"]) {
    await clickText(tab, `切换${tab}`);
    await page.waitForTimeout(1100);
    await state(`nodes-${tab}`);
  }
  const nodeSearch = await page.evaluate(() => {
    const el = [...document.querySelectorAll("input")].find((node) =>
      /搜索|过滤|class_type/.test(node.placeholder || ""),
    );
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  if (nodeSearch) {
    await page.mouse.click(nodeSearch.x, nodeSearch.y, {
      label: "聚焦节点搜索",
    });
    await page.keyboard.type("KSampler");
    await page.waitForTimeout(500);
    await state("nodes-search-KSampler", "table");
  }
  await shot("ui-followup-nodes");

  await page.goto("http://localhost:15173/workflows", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1200);
  for (const tab of ["本地清单", "同步状态", "运行统计"]) {
    await clickText(tab, `切换工作流${tab}`);
    await page.waitForTimeout(900);
    await state(`workflows-${tab}`);
  }
  await shot("ui-followup-workflows");

  await page.goto("http://localhost:15173/media", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1300);
  for (const tab of ["全部", "图片", "视频"]) {
    await clickText(tab, `切换媒体${tab}`);
    await page.waitForTimeout(800);
    await state(`media-${tab}`);
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
    await shot("ui-followup-media-detail");
    await page.keyboard.press("Escape");
  }

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
  await shot("ui-followup-cost");

  await page.goto("http://localhost:15173/logs", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(1200);
  await state("logs-requests");
  await clickText("实时控制台", "打开实时控制台");
  await page.waitForTimeout(1200);
  await state("logs-console");
  await shot("ui-followup-logs");

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
    await page.waitForTimeout(1800);
    await state("status-after-selfcheck");
  }

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
    await page.waitForTimeout(tab === "设置同步" ? 1200 : 700);
    await state(`settings-${tab}`);
  }
  await shot("ui-followup-settings");
} catch (error) {
  results.push({
    name: "fatal",
    fatal: String(error),
    stack: error?.stack ?? null,
  });
  try {
    await shot("ui-followup-fatal");
  } catch {}
} finally {
  const fs = await import("node:fs/promises");
  const path = `${evidence}/ui-followup-results.json`;
  await fs.writeFile(
    path,
    JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        scope: "ego-browser UI click follow-up; no prompt submission",
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

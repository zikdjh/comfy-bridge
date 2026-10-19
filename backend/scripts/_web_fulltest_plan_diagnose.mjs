const evidence =
  "C:/Users/z2351/.codex/visualizations/2026/10/15/01a13d51-a08d-7370-85c3-888b64255c42/ego/comfy-bridge-web-20261018";
const task = await taskSpace(1);
const page = task.page("p1");
const fs = await import("node:fs/promises");
await page.goto(
  "file:///E:/JAVA/work/web/zc/modal/comfy-bridge/docs/ComfyBridge-过程-全量Web测试计划-20261018.html",
  { waitUntil: "domcontentloaded", timeout: 30000 },
);
await page.waitForSelector("tr[data-id]", {
  state: "attached",
  timeout: 15000,
});
const result = await page.evaluate(() => {
  const source = [...document.scripts]
    .map((script) => script.textContent || "")
    .join("\n");
  const groupPattern =
    /mount:\s*"([^"]+)"[\s\S]*?prefix:\s*"([^"]+)"[\s\S]*?title:\s*"([^"]+)"[\s\S]*?rows:\s*\[([\s\S]*?)\n\s*\]\s*\}/g;
  const sourceGroups = [];
  let match;
  while ((match = groupPattern.exec(source))) {
    const rows = JSON.parse(`[${match[4]}]`);
    sourceGroups.push({
      mount: match[1],
      prefix: match[2],
      title: match[3],
      rows: rows.length,
      ids: rows.map((row) => row[2].slice(0, 80)),
    });
  }
  const rendered = [...document.querySelectorAll("tr[data-id]")].map(
    (row) => row.dataset.id,
  );
  const renderedSet = new Set(rendered);
  const offsets = {};
  const expected = [];
  for (const group of sourceGroups) {
    const offset = offsets[group.prefix] || 0;
    for (let index = 0; index < group.rows; index++) {
      expected.push(
        `${group.prefix}-${String(offset + index + 1).padStart(3, "0")}`,
      );
    }
    offsets[group.prefix] = offset + group.rows;
  }
  const expectedSet = new Set(expected);
  return {
    sourceGroups,
    sourceTotal: sourceGroups.reduce((total, group) => total + group.rows, 0),
    renderedTotal: rendered.length,
    missing: expected.filter((id) => !renderedSet.has(id)),
    unexpected: rendered.filter((id) => !expectedSet.has(id)),
    storage: JSON.parse(
      localStorage.getItem("cb_web_fulltest_plan_20261018_v1") || "{}",
    ),
    sampleRows: [...document.querySelectorAll("tr[data-id]")]
      .slice(0, 4)
      .map((row) => ({
        id: row.dataset.id,
        status: row.dataset.status,
        selected: row.querySelector("select")?.value,
        actual: row.querySelector("textarea[data-field=actual]")?.value,
        options: row.querySelector("select")?.innerText,
      })),
  };
});
const path = `${evidence}/plan-diagnose.json`;
await fs.writeFile(path, JSON.stringify(result, null, 2), "utf8");
console.log(
  JSON.stringify(
    {
      path,
      sourceTotal: result.sourceTotal,
      renderedTotal: result.renderedTotal,
      missing: result.missing,
      unexpected: result.unexpected,
      groups: result.sourceGroups.map((group) => ({
        prefix: group.prefix,
        rows: group.rows,
        title: group.title,
      })),
      sampleRows: result.sampleRows,
    },
    null,
    2,
  ),
);

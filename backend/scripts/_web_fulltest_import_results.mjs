const evidence =
  "C:/Users/z2351/.codex/visualizations/2026/10/15/01a13d51-a08d-7370-85c3-888b64255c42/ego/comfy-bridge-web-20261018";
const task = await taskSpace(1);
const page = task.page("p1");
const fs = await import("node:fs/promises");
const record = JSON.parse(
  await fs.readFile(
    `${evidence}/comfybridge-web-test-record-20261018.json`,
    "utf8",
  ),
);

await page.goto(
  "file:///E:/JAVA/work/web/zc/modal/comfy-bridge/docs/ComfyBridge-过程-全量Web测试计划-20261018.html",
  { waitUntil: "domcontentloaded", timeout: 30000 },
);
await page.waitForSelector("tr[data-id]", { state: "attached" });
const before = await page.evaluate(() => ({
  rows: document.querySelectorAll("tr[data-id]").length,
  completion: document.getElementById("completion")?.innerText,
  duplicateTables: [...document.querySelectorAll(".table-wrap")].length,
}));
await page.evaluate(
  ({ key, records }) => localStorage.setItem(key, JSON.stringify(records)),
  {
    key: "cb_web_fulltest_plan_20261018_v1",
    records: record.records,
  },
);
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForSelector("tr[data-id]", { state: "attached" });
const after = await page.evaluate(() => ({
  rows: document.querySelectorAll("tr[data-id]").length,
  completion: document.getElementById("completion")?.innerText,
  duplicateTables: [...document.querySelectorAll(".table-wrap")].length,
  statusCounts: [...document.querySelectorAll("tr[data-id]")].reduce((counts, row) => {
    const status = row.dataset.status || "todo";
    counts[status] = (counts[status] || 0) + 1;
    return counts;
  }, {}),
  failed: [...document.querySelectorAll('tr[data-status="fail"]')].map((row) => ({
    id: row.dataset.id,
    text: row.innerText.slice(0, 1000),
  })),
}));
const screenshot = `${evidence}/ui-followup3-plan-import.png`;
await page.screenshot({ path: screenshot });

const jsonDownload = page.waitForEvent("download", { timeout: 30000 });
await page.click("#export-json");
const json = await jsonDownload;
const jsonPath = `${evidence}/ui-export-plan-record.json`;
await json.saveAs(jsonPath);

const csvDownload = page.waitForEvent("download", { timeout: 30000 });
await page.click("#export-csv");
const csv = await csvDownload;
const csvPath = `${evidence}/ui-export-plan-record.csv`;
await csv.saveAs(csvPath);

const result = {
  before,
  after,
  screenshot,
  json: { path: jsonPath, filename: json.suggestedFilename() },
  csv: { path: csvPath, filename: csv.suggestedFilename() },
};
const outputPath = `${evidence}/ui-followup3-import-results.json`;
await fs.writeFile(outputPath, JSON.stringify(result, null, 2), "utf8");
console.log(JSON.stringify({ outputPath, ...result }, null, 2));

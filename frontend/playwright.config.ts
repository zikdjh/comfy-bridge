/**
 * Playwright 全栈 E2E：真实后端（CB_CONFIG_DIR 隔离的离线默认配置，无需
 * ComfyUI/Modal）+ 嵌入式构建产物。
 *
 * - webServer 自动拉起后端（默认 ../backend/.venv 的 Python，CI 用 uv sync 生成
 *   同一路径；可用 E2E_PYTHON 覆盖），就绪探测 /api/v1/health。
 * - 浏览器默认用系统 Edge（本地与 Windows CI 都预装，免 Chromium 下载），可用
 *   E2E_BROWSER 覆盖（如 chromium，需先 playwright install）。
 * - 端口固定 18199，避开生产 18189-18193 与 Vite 15173；CI 上 reuseExistingServer 关闭。
 */
import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const PORT = 18199;
const BASE = `http://127.0.0.1:${PORT}`;
const python = process.env.E2E_PYTHON ?? "../backend/.venv/Scripts/python.exe";

export default defineConfig({
  testDir: "./e2e",
  // CI/默认门禁只跑隔离后端的核心链路；多提交/队列进度两条依赖
   // 手工 18189 真机环境，使用独立配置或显式 testMatch 运行。
  testMatch: /flows\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  outputDir: resolve("test-results"),
  use: {
    baseURL: BASE,
    channel: process.env.E2E_BROWSER ?? "msedge",
    viewport: { width: 1280, height: 720 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${python}" -m uvicorn --app-dir ../backend app.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `${BASE}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      // 配置/历史/缓存整体重定向，保证离线空态的确定性并与真实用户配置隔离
      CB_CONFIG_DIR: resolve("e2e/.tmp-config"),
      CB_DATA_DIR: resolve("e2e/.tmp-config/data"),
      // 空凭证覆盖系统环境；避免隔离测试意外读取真实 Modal 凭证访问云端卷
      MODAL_TOKEN_ID: "",
      MODAL_TOKEN_SECRET: "",
    },
  },
});

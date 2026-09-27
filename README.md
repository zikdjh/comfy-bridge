# ComfyBridge

本地 ComfyUI × Modal 云端 GPU 桥接器：拦截本地生成请求并按策略转发到 Modal 执行，
同步两端节点/配置/工作流（不同步模型），管理 Modal 数据卷，统一记录日志与成本。

- 管理线（范围/排期/风险/验收）：`docs/ComfyBridge项目计划书.html`
- 技术线（架构/核心设计/API/目录）：`docs/ComfyUI-Modal桥接器项目规划.html`
- 速查：`docs/ComfyUI启动命令与参数参考.html`、`docs/Modal云平台与SDK参考.html`

## 目录

```
backend/       FastAPI 后端：数据面反代拦截 + /api/v1 管理面 + 日志/成本
frontend/      React 控制台（开发期经 Vite dev server 访问；构建产物拷入 backend/app/static，后端挂载随 M4 接入）
modal-runner/  Modal 云端 Runner App（runner.py 由原 comfyui_app.py 演进）
e2e/           Playwright 端到端测试（预留，当前为空）
docs/          项目文档
scripts/       dev.ps1 / build.ps1
```

## 快速开始（开发）

```powershell
# 后端（首次：uv sync 安装依赖）
cd backend
uv run uvicorn app.main:app --reload --port 8189

# 前端（另开终端，首次：pnpm install）
cd frontend
pnpm dev          # http://127.0.0.1:5173，/api 代理到 8189

# 或一键并行启动
powershell -File scripts/dev.ps1
```

后端配置：复制 `backend/config.example.toml` 到 `~/.comfybridge/config.toml` 按需修改。
管理台入口 `http://127.0.0.1:5173`；出图走桥接地址 `http://127.0.0.1:8189`
（浏览器指向 :8189 即透明使用本地 ComfyUI，生成请求经拦截层按路由规则分流本地/云端）。

## 部署（Modal 云端 Runner）

```powershell
modal deploy modal-runner/runner.py   # 常驻部署，输出固定 Web URL
modal serve  modal-runner/runner.py   # 临时调试，改码自动重载
```

- 部署后把 Web URL 与 Modal API Token 填入管理台「设置 → Modal」，或写入
  `~/.comfybridge/config.toml` 的 `[modal]`（凭证只存用户目录配置文件或环境变量，绝不写入代码库）。
- 模型/自定义节点/输入输出/工作流全部持久化在 Modal 数据卷（默认 `comfyui-data`，挂载到容器 `/data`）；
  runner 冷启动自动补装 custom_nodes 依赖，数据卷内容经管理台「数据卷」页管理。
- Modal 执行通道无需常驻：按需冷启动（仪表盘可见通道状态，「预热」按钮可提前拉起容器），空闲自动缩容到 0。

## 打包（M4）

```powershell
powershell -File scripts/build.ps1   # 构建前端并拷入 backend/app/static
```

后端目前不挂载 `backend/app/static`（控制台请走 Vite dev server）；静态托管接入随 M4 打磨发布落地。

## 里程碑

M0 技术验证收尾 → M1 MVP 桥接 → M2 同步引擎 → M3 模型卷管理 → M4 打磨发布。
当前进度与验收标准见计划书 §6–§7。

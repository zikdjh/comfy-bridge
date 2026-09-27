# ComfyBridge

本地 ComfyUI × Modal 云端 GPU 桥接器：拦截本地生成请求并按策略转发到 Modal 执行，
同步两端节点/配置/工作流（不同步模型），管理 Modal 数据卷，统一记录日志与成本。

- 管理线（范围/排期/风险/验收）：`docs/ComfyBridge项目计划书.html`
- 技术线（架构/核心设计/API/目录）：`docs/ComfyUI-Modal桥接器项目规划.html`
- 速查：`docs/ComfyUI启动命令与参数参考.html`、`docs/Modal云平台与SDK参考.html`

## 目录

```
backend/       FastAPI 后端：数据面反代拦截 + /api/v1 管理面 + 日志/成本
frontend/      React 控制台（构建产物嵌入 backend/app/static）
modal-runner/  Modal 云端 Runner App（runner.py 由原 comfyui_app.py 演进）
e2e/           Playwright 端到端测试
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

## 里程碑

M0 技术验证收尾 → M1 MVP 桥接 → M2 同步引擎 → M3 模型卷管理 → M4 打磨发布。
当前进度与验收标准见计划书 §6–§7。

# ComfyBridge

本地 ComfyUI × Modal 云端 GPU 桥接器：拦截本地生成请求并按策略转发到 Modal 执行，
同步两端节点/配置/工作流（不同步模型），管理 Modal 数据卷，统一记录日志与成本。

- 管理线（范围/排期/风险/验收）：`docs/ComfyBridge项目计划书.html`
- 技术线（架构/核心设计/API/目录）：`docs/ComfyUI-Modal桥接器项目规划.html`
- 速查：`docs/ComfyUI启动命令与参数参考.html`、`docs/Modal云平台与SDK参考.html`

## 目录

```
backend/       FastAPI 后端：数据面反代拦截 + /api/v1 管理面 + 日志/成本
frontend/      React 控制台（开发期经 Vite dev server 访问；构建产物嵌入 backend/app/static，由后端挂载 /bridge）
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

## 打包分发（M4）

```powershell
cd backend
uv run python scripts/build_package.py   # 前端嵌入构建 → modal-runner 副本 → uv build → 产物自检
```

产物 `backend/dist/comfy_bridge-*.whl`（内含 GUI 构建产物与 modal-runner 副本），安装即用：

```powershell
pipx install backend/dist/comfy_bridge-0.1.0-py3-none-any.whl
comfy-bridge --port 8189   # 控制台 http://127.0.0.1:8189/bridge/
```

- 管理面 GUI 挂在 `/bridge`（SPA 回退）；`/` 与其余路径仍是本地 ComfyUI 数据面透传，**出图地址不变**。
- 安装包自带 modal-runner 副本：节点同步 / 版本对齐 / 数据卷作业 / `modal deploy` 在安装态均可直接用。
- `scripts/build.ps1` 仅做前端嵌入构建（`pnpm build:embed` → `backend/app/static`，开发后端同样挂 /bridge）。
- 依赖注：modal 1.6+ 在系统代理环境硬要求 python-socks，已直接声明，代理环境开箱可用。

## 里程碑

M0 技术验证收尾 → M1 MVP 桥接 → M2 同步引擎 → M3 模型卷管理 → M4 打磨发布。

当前进度（2026-09-30）：桥接主链路可用（反代拦截 / 路由干跑 / Modal 执行通道 / 日志与控制台 / 数据卷管理）；M2 进行中：

- **节点清单与卷同步已接入**（M2-1 部分 + M2-3 卷模式）：本地 `custom_nodes/` 扫描（git remote/commit、依赖声明、
  启停状态）与云端数据卷清单对比，差异矩阵给出 `same / changed / missing_cloud / missing_local`，含 commit 与启用状态
  一致性判定；可选 `deep=1` 拉云端 `/object_info` 做 class_type 级对比（会冷启动容器，仅手动触发）。「安装到云端」
  把节点目录/单文件写入数据卷并维护 `custom_nodes/.comfybridge-manifest.json`，云端清单经 `modal volume get`
  免容器读取。「从云端移除」同步清理 manifest。停用节点整目录以 `.disabled` 后缀落卷（云端加载与装依赖均跳过）。
  镜像固化（M2-2：`nodes.lock.yaml` + `install_nodes.sh` + `modal deploy` + 失败回滚）与 Manager API 兜底采集待做。
- **设置键级推拉已接入**（M2-5 部分：两端 comfy.settings.json 键级 diff 与按方向执行，见「设置 → 设置同步」）。
- **额度使用与监控已接入**（M4-1 部分）：云端请求按次入账（GPU 单价 × 墙钟秒数，含冷启动等待），
  `GET /api/v1/cost/summary` 聚合今日/本月/近 30 天；月度预算护栏超限自动回落本地或拒绝（决策留痕 `budget_guardrail` +
  控制台告警）；仪表盘「额度与成本」卡片、顶栏预算角标、日志页成本列、设置页预算表单。另接入 Modal 账单实时查询
  （modal CLI `billing summary` 子进程，120s 缓存；CLI 即 SDK `Workspace.billing.summary` 的官方前端，1.5+ 可用）：
  账户级本期计量消耗/积分抵扣/实际计费（UTC 月），
  「剩余积分总额」Modal 无公开接口暂不展示。
- 工作流双向同步（M2-4）、版本对齐作业（M2-5 余项）、成本报表升级与逐项账单对账（M4-1 余项）待做。

里程碑验收标准见计划书 §6–§7。

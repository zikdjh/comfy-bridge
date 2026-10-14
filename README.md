# ComfyBridge

本地 ComfyUI × Modal 云端 GPU 桥接器：拦截本地生成请求并按策略转发到 Modal 执行，
同步两端节点/配置/工作流（不同步模型），管理 Modal 数据卷与多账号档案切换
（1 账号 = 1 份凭据），统一记录日志与成本。

- 管理线（范围/排期/风险/验收）：`docs/ComfyBridge-计划-项目计划书.html`
- 技术线（架构/核心设计/API/目录）：`docs/ComfyBridge-计划-技术规划.html`
- 缺陷与修复入口：`docs/ComfyBridge-过程-真机验收缺陷清单.html`（真机验收残留项登记）
- 测试计划：docs/ComfyBridge-过程-第二轮完整测试计划.html（第二轮完整回归 + 性能压测执行清单）
- SDK 迁移记录：docs/ComfyBridge-设计-ModalSDK迁移.html（控制面已切 Python SDK；deploy / 流式 run / container 保留 CLI）
- 速查：`docs/ComfyBridge-参考-ComfyUI启动命令与参数.html`、`docs/ComfyBridge-参考-Modal云平台与SDK.html`

## 目录

```
backend/       FastAPI 后端：数据面反代拦截 + /api/v1 管理面 + 日志/成本
frontend/      React 控制台（开发期经 Vite dev server 访问；构建产物嵌入 backend/app/static，由后端挂载 /bridge）
modal-runner/  Modal 云端 Runner App（runner.py 由原 comfyui_app.py 演进）
frontend/e2e/  Playwright 全栈端到端测试（离线隔离配置，覆盖管理台核心链路）
docs/          项目文档
scripts/       dev.ps1 / build.ps1
```

## 快速开始（开发）

```powershell
# 后端（首次：uv sync 安装依赖）
cd backend
uv run uvicorn app.main:app --reload --port 8189

# 前端（另开终端，首次：pnpm install；需 Node ≥ 18 且 node.exe 在 PATH）
cd frontend
pnpm dev          # http://127.0.0.1:5173，/api 代理到 8189

# 或一键并行启动
powershell -File scripts/dev.ps1
```

Node 环境排查：管理台「设置 → 本地」的 Node.js 环境卡片可自动检测
（手动指定路径 → PATH → 常见安装目录快扫）；`node.exe` 不在 PATH 时在卡片里
手动指定路径保存即可，对应 API `GET /api/v1/config/node-env`
（配置项 `~/.comfybridge/config.toml` 的 `[local].node_path`）。

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
- modal 依赖：控制面数据（卷读写、应用状态、账单）走 Python SDK 直调；
  `modal deploy`、流式 `modal run`、`container list/stop` 仍走 CLI。
  1.6+ 在系统代理环境硬要求 python-socks，已直接声明，代理环境开箱可用。

### 大模型分块上传

模型页除本地 ↔ 云端同步外，还提供“分块上传（断点续传 + MD5 校验）”：

- 浏览器按 8 MiB 分块上传到桥接器，分块和服务端会话元数据暂存在
  `~/.comfybridge/uploads/<upload_id>/`（或 `CB_CONFIG_DIR` 指定的配置目录）。
- 上传中断、浏览器刷新或后端重启后，只要重新选择同一文件，就能从服务端已确认的分块继续。
- 全部分块到齐后，服务端按序合并、计算整文件 MD5；只有与客户端声明的 MD5 一致才通过
  Python SDK 写入 `models/<分类>/<文件名>`。
- MD5 不一致、分块损坏或落卷失败都会保留失败原因，页面可查看并重试；校验失败需要删除会话后重传。

### 模型删除保护与回收站

数据卷页删除模型文件前会检查近期请求、图快照和本地工作流引用；命中时先展示来源并
要求二次确认。模型文件采用软删除，移入卷内 `.trash/<UTC 时间戳>/<原路径>`，
默认保留 7 天，可在数据卷页的回收站卡片中恢复或永久清理。

删除被引用的模型时必须显式选择“仍要软删除”；回收站恢复会拒绝覆盖目标位置的同名
条目。后台任务每小时清理一次过期回收站，只处理合法时间戳目录。

## 测试

```powershell
# 后端单元/集成/兼容矩阵（默认不跑 live）
cd backend
uv run pytest -q

# 前端类型检查与构建
cd frontend
pnpm build

# 离线管理台 Playwright e2e（自动拉起隔离后端，默认使用系统 Edge）
cd frontend
pnpm exec playwright test
```

真实本地 ComfyUI + Modal Runner 的发布前验收用例标记为 `live`，默认 skip，运行方式见
`backend/tests/live/README.md`。未设置 `CB_LIVE=1` 时不会访问真实两端或产生 Modal 费用。

## 管理面访问边界

桥接器默认只监听 `127.0.0.1`，本机使用无需额外认证。若要用 `--host 0.0.0.0`
或局域网地址对外监听，必须配置管理令牌，否则 `comfy-bridge` 会拒绝启动：

```powershell
# 方式一：环境变量
$env:CB_MANAGEMENT_TOKEN = "<随机长令牌>"
comfy-bridge --host 0.0.0.0 --port 8189

# 方式二：写入用户配置目录（默认 ~/.comfybridge/management_token）
Set-Content -Path "$HOME\.comfybridge\management_token" -Value "<随机长令牌>" -NoNewline
```

配置令牌后，`/api/v1`（`/api/v1/health` 除外）与 `/bridge` 需要认证：
浏览器访问管理台会弹 Basic 认证；脚本可发送 `Authorization: Bearer <令牌>`。
数据面 ComfyUI 透传端点不受该中间件拦截，但仍建议只在本机或可信网络内使用。

配置令牌后还会启用两项边界保护（P2-1）：

- **Origin 校验**：跨站 `Origin` 的管理请求返回 403（CSRF 防护）；独立前端域名需加入
  `CB_ALLOWED_ORIGINS`（逗号分隔）。
- **审计日志**：写操作与所有被拒请求记入 `<配置目录>/audit.log`（JSONL，5MB 轮转），
  只记方法/路径/状态码/客户端 IP/Origin/认证方式，**不含 token 与请求体**。

对外部署（反代、Origin 白名单、审计开关、边界检查清单）见
[`docs/ComfyBridge-运维-反向代理与多用户部署.html`](docs/ComfyBridge-运维-反向代理与多用户部署.html)。

### 单实例限制（P2-2）

当前版本是**单机单实例**：同一配置目录、同一后端实例、单进程。请求日志与成本
存本地 SQLite，作业状态（同步、卷上传下载、卷移动、镜像部署、本机扫描）存进程
内存，后端重启后历史作业清空、在途记录会收尾为 cancelled。不要启动多个后端
进程共享同一个 `CB_CONFIG_DIR` / `~/.comfybridge`，也不要对 `/api/v1` 做多实例
负载均衡。

需要多实例时，应先把状态迁到共享存储（PostgreSQL / Redis；事件流同理换
Redis Pub/Sub），再考虑横向扩展。作业状态已抽出统一接缝
`app/core/job_store.py`：`sync/jobs.py` 与 `modal_volume/jobs.py` 已改用
`JobStore` 协议，未来替换 Redis / PostgreSQL 实现无需重写业务模块。

### 预算护栏边界（P2-4）

管理台的「月度预算上限」是 **ComfyBridge 本地估算护栏**，不是 Modal 账户级
硬上限，也无法阻止绕过桥接器的消费：

- 判定依据是本地按次估算：`gpu_hourly_rate_usd × 请求墙钟秒数`（含冷启动等待），
  只覆盖经桥接器分流到 Modal 的请求；`modal serve/deploy`、其他客户端、CPU/内存/
  数据卷等其他资源都不计入。
- 单价是配置值，Modal 调价后需在设置页同步更新；成本报表页的「账单对账」会显示
  `Modal GPU 计量 ÷ 本地估算`，偏离 1 过大时会主动提示校准。
- 需要账户级止损时，请在 Modal 平台配置预算 / 消费告警与额度上限；本地护栏与
  平台侧预算配合使用：本地护栏负责即时回落或拒绝，平台侧负责全局兜底。

`GET /api/v1/cost/summary` 的 `budget`、路由决策的 `budget_guardrail` 留痕与
控制台告警都沿用同一估算口径，界面文案已明确标注「本地护栏」。

### 云端提交前隐私确认（P2-5）

首次把工作流分流到 Modal 时，桥接器会先返回一次数据边界说明，明确哪些数据会
离开本机：

- **工作流图快照**：节点、连线、参数（可能含提示词、路径、模型名）；
- **输入文件**：`LoadImage` 等节点引用的本地图片/音频；
- **必要元数据**：工作流名、客户端 ID、路由决策留痕；
- **云端执行日志**：容器 stdout 与执行事件。

模型权重仍留在 Modal 数据卷，不会因确认而上传整个本地磁盘。确认流程是
**无状态**的：后端只在该次请求缺少 `X-Bridge-Privacy-Confirmed: 1` 且开关开启时
返回 `409 privacy_confirmation_required`；浏览器页面由桥接器注入的一次性脚本
弹窗确认并带重试，脚本按浏览器记在 localStorage。`设置 → 桥接 → 云端提交前
隐私确认` 可随时关闭或重新开启（对应 `bridge.privacy_confirm`）。

## 许可证

ComfyBridge 采用 [MIT License](LICENSE)。

桥接器只通过 HTTP/WebSocket API 与本地或 Modal 容器中的 ComfyUI 独立进程交互，
不复制、不静态链接也不动态链接 ComfyUI 源码或二进制。ComfyUI 本体及其第三方节点
仍分别受各自许可证约束；若后续要从 GPL 组件复制代码，需要单独评审并保留相应许可。

## 里程碑

M0 技术验证收尾 → M1 MVP 桥接 → M2 同步引擎 → M3 模型卷管理 → M4 打磨发布。

**当前状态（2026-10-11）**：M0–M4 均已落地，可安装、可验证、边界清楚；发布前仍需在
真实两端跑一次 live 冒烟与干净机器安装验收（见 `backend/tests/live/README.md`）。

**核心链路**

- 反代拦截 `/prompt` + WS 中继 + 路由决策链（默认/video/尺寸/自定义规则/缺节点/缺模型/预算/并发）。
- 本地 ⇄ 云端：工作流、节点、设置键级同步；模型卷浏览、上传（分块续传 + MD5）、下载、移动、软删除与回收站。
- 云端 Runner：代理鉴权、镜像固化（`nodes.lock.yaml`）、ComfyUI 版本对齐与回滚、部署任务与历史留痕。
- 运维面：请求日志/成本报表/账单对账/额度护栏、统一作业事件流与任务抽屉、系统状态页与 Modal SDK 故障分类。
- 边界：管理面令牌/Origin/审计日志、反代部署说明、单实例限制说明、预算护栏边界、云端提交前隐私确认。

**最近变更**

- LV-06 重启清理修复：`modal_pid` 持久化 + 启动对账（云端在跑请求重启后收敛到
  真实结局并补时长，成功时补回传产物）。
- 真机验收（本地 v0.39.2 ↔ 云端 pin 不同）：修掉 modal CLI 编码崩溃、热容器
  volume 快照读不到新输入、纯 HTTP 无终态、WS 泵阻塞导致中断失效四处缺陷，
  版本对齐与节点同步实测通过；详见 `docs/ComfyBridge-过程-真机验收缺陷清单.html` 与 `FIXLIST.md` §三十四。
- P2-1 管理面鉴权边界（Origin + 审计日志 + 反代文档）。
- P3-1 ruff 与 mypy 存量清零，CI 改为零违规、零类型错误门禁。
- P2-2 作业状态存储接缝（`app/core/job_store.py`）与单实例边界文档。
- P2-4 预算护栏边界与账单对账偏差告警。
- P2-5 云端提交前数据边界确认与可撤销开关。

更早的实现细节与真机验证记录保留在 `FIXLIST.md`；里程碑验收标准见计划书 §6–§7。

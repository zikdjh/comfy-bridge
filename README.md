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
frontend/e2e/  Playwright 全栈端到端测试（离线隔离配置，覆盖管理台核心链路）
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

### 大模型分块上传

模型页除本地 ↔ 云端同步外，还提供“分块上传（断点续传 + MD5 校验）”：

- 浏览器按 8 MiB 分块上传到桥接器，分块和服务端会话元数据暂存在
  `~/.comfybridge/uploads/<upload_id>/`（或 `CB_CONFIG_DIR` 指定的配置目录）。
- 上传中断、浏览器刷新或后端重启后，只要重新选择同一文件，就能从服务端已确认的分块继续。
- 全部分块到齐后，服务端按序合并、计算整文件 MD5；只有与客户端声明的 MD5 一致才调用
  `modal volume put` 写入 `models/<分类>/<文件名>`。
- MD5 不一致、分块损坏或落卷失败都会保留失败原因，页面可查看并重试；校验失败需要删除会话后重传。

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

## 许可证

ComfyBridge 采用 [MIT License](LICENSE)。

桥接器只通过 HTTP/WebSocket API 与本地或 Modal 容器中的 ComfyUI 独立进程交互，
不复制、不静态链接也不动态链接 ComfyUI 源码或二进制。ComfyUI 本体及其第三方节点
仍分别受各自许可证约束；若后续要从 GPL 组件复制代码，需要单独评审并保留相应许可。

## 里程碑

M0 技术验证收尾 → M1 MVP 桥接 → M2 同步引擎 → M3 模型卷管理 → M4 打磨发布。

当前进度（2026-10-02）：桥接主链路可用（反代拦截 / 路由干跑 / Modal 执行通道 / 日志与控制台 / 数据卷管理）；
M2 同步引擎主体、M3 规则链数据化、M4 护栏与打包分发均已落地：

- **节点清单与卷同步已接入**（M2-1 部分 + M2-3 卷模式）：本地 `custom_nodes/` 扫描（git remote/commit、依赖声明、
  启停状态）与云端数据卷清单对比，差异矩阵给出 `same / changed / missing_cloud / missing_local`，含 commit 与启用状态
  一致性判定；可选 `deep=1` 拉云端 `/object_info` 做 class_type 级对比（会冷启动容器，仅手动触发）。「安装到云端」
  把节点目录/单文件写入数据卷并维护 `custom_nodes/.comfybridge-manifest.json`，云端清单经 `modal volume get`
  免容器读取。「从云端移除」同步清理 manifest。停用节点整目录以 `.disabled` 后缀落卷（云端加载与装依赖均跳过）。
  镜像固化已接入（M2-2）：`nodes.lock.yaml` + `install_nodes.sh` 把启用节点依赖预装进镜像（`modal deploy` 重建，
  失败自动回滚）；单文件节点一致性按内容 sha256 判定（2026-10-01）；diff 持久化缓存与 ComfyUI-Manager API
  兜底采集已接入（2026-10-02，见「M2-1 余项收尾」）。ComfyUI 版本对齐已接入（M2-5 第一片）：本地版本为权威，
  云端 pin 追随本地并自动重建镜像，失败回滚 pin。
- **工作流双向同步已接入**（M2-4）：本地 ⇄ 云端用户卷按文件同步，三方变更检测（本地/云端/基线）+ 冲突双副本
  （`name.conflict-时间戳.json`），修复 `volume ls` 中文双重转义。changed 内容哈希检测、反向同步提示、冲突双列对比
  已于 2026-10-01 收尾落地（见「M2 收尾」）；运行统计已真实化（2026-10-02，见「零星项收尾」）。
- **Runner 端点鉴权已接入**（P0 安全）：web_server 开启 `requires_proxy_auth`，桥接侧 HTTP/WS 统一注入
  Modal-Key/Secret 代理凭证；设置页独立凭证族字段。云端最大并发护栏已接入（M4-1 收尾）：在途云端任务计数达上限
  按不可用动作回落/拒绝。
- **路由规则链数据化已接入**（M3-1/M3-2）：自定义规则链（workflow_tag / gen_type / min_pixels /
  node_missing_local / node_missing_cloud）存配置可 CRUD 排序，路由页可编辑；node_presence 节点 class 两端在场
  缓存（持久化 + TTL 单飞），本地/云端缺节点自动改道，支持强制刷新。
- **打包分发已接入**（M4）：`backend/scripts/build_package.py` 一键构建 wheel（GUI 产物嵌入 `/bridge` 挂载 + SPA
  回退、modal-runner 包内副本双布局解析、`comfy-bridge` 启动命令），pipx 安装即用；见「打包分发（M4）」节。
- **设置键级推拉已接入**（M2-5 部分：两端 comfy.settings.json 键级 diff 与按方向执行，见「设置 → 设置同步」）。
- **额度使用与监控已接入**（M4-1 部分）：云端请求按次入账（GPU 单价 × 墙钟秒数，含冷启动等待），
  `GET /api/v1/cost/summary` 聚合今日/本月/近 30 天；月度预算护栏超限自动回落本地或拒绝（决策留痕 `budget_guardrail` +
  控制台告警）；仪表盘「额度与成本」卡片、顶栏预算角标、日志页成本列、设置页预算表单。另接入 Modal 账单实时查询
  （modal CLI `billing summary` 子进程，120s 缓存；CLI 即 SDK `Workspace.billing.summary` 的官方前端，1.5+ 可用）：
  账户级本期计量消耗/积分抵扣/实际计费（UTC 月），
  「剩余积分总额」Modal 无公开接口暂不展示。
- **成本报表 v2 已接入**（M4-1）：「成本报表」页按工作流/主模型维度聚合近 30 天成本（主模型 = 图内
  checkpoint/扩散模型权重引用，历史请求启动时从图快照回填）；`GET /api/v1/cost/report`、
  `GET /api/v1/cost/export`（CSV 导出：逐条流水/聚合，utf-8-sig）；`GET /api/v1/cost/reconciliation`
  逐项账单对账（本地按次估算 vs Modal `billing report` 明细 GPU 资源计量，UTC 月 + UTC 日界，比值持续偏离 1 提示校准单价）。
- **M2 收尾已接入**（2026-10-01）：节点页「重建镜像」合并固化与版本对齐为一次 modal deploy（此前两次）；
  节点 diff 单文件节点以两端内容 sha256 判漂移，云端独有节点给安装命令线索（反向同步提示，不自动改本地环境）；
  工作流冲突行新增双列对比（两端大小/hash/节点类型/模型引用摘要，`GET /api/v1/workflows/compare`）。
- **零星项收尾已接入**（2026-10-02）：工作流页「运行统计」Tab 真实数据（`GET /api/v1/workflows/run-stats`，
  requests 按工作流 SQL 现算聚合：次数/成败/本地·云端拆分/平均与最大耗时/估算成本/最近运行，近 7/30/90 天切换）；
  云端容器数面板（`GET /api/v1/launch/modal/containers`，45s 进程内缓存、停止容器即时失效，
  设置·启动命令与仪表盘展示）；首次配置向导（五步：检测 → Modal 凭证 → 本地 ComfyUI → 云端 Runner → 完成，
  Shell 凭证缺失时自动弹一次 + 顶部提示条，设置页入口可重跑；config 视图补回显 web_url/runner_enabled）。
- **提交前预校验已接入**（M3-5 余项，2026-10-02）：`/prompt` 提交前按目标端复刻缺节点/缺模型检查提前拦截
  （零执行零费用，节点/模型索引不可用自动降级放行），供给策略 `bridge.preflight` 四档 off/strict/ask/auto
  （auto：缺模型且本地有副本时自动上传云端卷后放行，剩余缺节点按 ask 拦截；供给清单/失败明细进 rule_trace）；
  拦截落库为独立 `preflight` 状态（日志页「预校验拦截」标签 + 筛选），设置页桥接 Tab 选择策略。
- **M2-1 余项收尾已接入**（2026-10-02）：节点 diff 持久化缓存——矩阵 120s TTL、deep class_type 块长期落盘
  （重启不丢，防重复冷启动计费；同步/移除后失效；缓存时刻在差异矩阵标注，「重新扫描」强制现算，实测现算 19s →
  命中 2s、deep 现算 77s → 重启后 1.5ms）；ComfyUI-Manager API 兜底采集（在线且装 Manager 时增强本地清单：
  非 git 包补 repo、git 包补注册表版本/展示名，离线/未装静默降级；本地清单表格新增版本列与 Manager 标签）。
- **同步任务 JobDrawer 已接入**（M2-6 余项，2026-10-03）：工作流同步与节点同步任务化——通用内存作业注册表
  （进度条目/日志/协作式取消/容量修剪，预检失败不开作业直接报错），`GET /api/v1/jobs` 查询与取消；
  前端统一任务抽屉（焦点视图 + 列表）+ 顶栏「任务」角标（空闲 15s/运行 3s 轮询，与页面共享查询缓存），
  两页完成回调保留原有结果弹窗与「重启容器生效」确认。
- **版本对齐历史留痕已接入**（M2-5 收尾，2026-10-03）：显式对齐与随镜像重建的 pin 变更均追加 JSONL 历史
  （`~/.comfybridge/version_align_history.jsonl`：时间/版本区间/结局 ok·已回滚·超时·无需变更/耗时/错误；
  deploy 一次性收尾回调直取真实结局，noop 也留痕，留痕失败静默不影响对齐）；`GET /api/v1/nodes/version`
  带最近 20 条，节点页新增「对齐历史」折叠面板。
- **兼容性矩阵测试已接入**（M4 余项，2026-10-03）：`backend/tests/compat` 离线夹具矩阵——锁定 v0.37.2/
  v0.38.0/v0.38.2 三个近期版本（上游源码 sha256 指纹溯源 manifest），40 用例逐版本钉死 /prompt 应答、
  WS 事件泵、history 三态分类、输出四桶回传、容器日志归一、object_info 顶层键集、本地 WS tap 与双通道跟踪
  （0.8s 离线跑完；升版 SOP：复制夹具目录 → 更新指纹 → 跑矩阵，负向验证漂移可捕获；实测三版 execution.py
  同哈希，当代协议面稳定）。

里程碑验收标准见计划书 §6–§7。

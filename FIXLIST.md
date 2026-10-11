# ComfyBridge 修复清单

> 整理时间：2026-09-27 · 分支 main · 基线 commit `9327e22`（初始 `38a1dd6` + 凭证空白修复）
> ~~P2 批次 = 当前**未提交**的 14 个文件改动（+123 / −276，删除大头来自 uv.lock 依赖再生）~~
> → **已分批提交**：`b2a75d8` 后端+runner / `92c142a` 前端 / `3f09472` 依赖清理 · 提交前凭证 grep 零命中
> 状态依据：两轮**实测核验**（git 足迹 / ASCII 关键字探针 / py_compile 全量 / pytest / 冒烟断言 / 浏览器 E2E），不是上一轮的"声称"。
> **收尾验证 2026-09-27 全部通过**：build+tsc 0 错 · curl 冒烟 5/5 · 浏览器 E2E 4/4 · pytest 18 passed —— 记录见第三节。
> **清单闭环 2026-09-27**：第二节待做项亦全部完成（`97c7571` 防双击 · `36d59db` zustand/死代码/CLI JSON 去重），
> 该轮验证：tsc+vite 0 错 · pytest 18 passed · 真实 CLI 冒烟 `overview ok:true` · 凭证 grep 零命中。
> 提交链：`9327e22` → `b2a75d8` → `92c142a` → `3f09472` → `d456f49` → `97c7571` → `36d59db` → `7e5af9a`。本清单已无未完成条目。
> **新增批次 2026-09-28（节点清单差异与卷同步）**：见第六节。验证摘要：pytest **29 passed** · `pnpm build`（tsc+vite）0 错 · `py_compile` 通过 · 隔离回放覆盖节点上传 + manifest 写入；真实 Modal 卷同步于 **2026-09-30 网络恢复后补验通过**（09-28 曾因 CLI 网络阻断搁置）。

## 一、P2 批次（✅ 已提交并验证：`b2a75d8` 后端+runner / `92c142a` 前端 / `3f09472` 清理）

### 后端（11 项 · 探针全部命中 · 56 个 py 文件编译 0 失败 · 18 单测通过）

- [x] `backend/app/runner/executor.py` — 输入上传拒绝绝对路径与 `..` 穿越；输出下载 `dest.resolve()` + `is_relative_to(out_root)` 越界防护（探针 is_relative_to=1、in p.parts=1；+12/−1）
- [x] `backend/app/proxy/object_info.py` — 上游读取显式 `timeout=20.0`（共享 client read=None，必须显式给）（探针 timeout=20=1）
- [x] `backend/app/proxy/ws_relay/__init__.py` — 畸形 comfy_url 防御 `"://" not in` 回退默认 ws 地址（探针命中）
- [x] `backend/app/api/v1/requests.py` — `total = store.count_requests()`，响应含 total/returned 语义（探针命中）
- [x] `backend/app/api/v1/logs.py` — modal 快照 `online` 改为 `runner_client.available()` 真实值（探针 available()=3）
- [x] `backend/app/logs/store.py` — `GRAPH_KEEP = 500` 图快照裁剪（+17/−0）
- [x] `backend/app/logs/local_stream.py` — 断开时 finally 取消 ComfyUI 侧订阅（client_ids×6；上轮已人工核对逻辑连贯）
- [x] `backend/app/api/v1/modal_volume.py` — 本地 rglob 扫描移入 `asyncio.to_thread`（+15/−8）
- [x] `modal-runner/runner.py` — ComfyUI 进程 watchdog 线程 + 自定义节点依赖安装 `timeout=300` 逐项日志（探针 _watchdog=2、timeout=300=1；+30/−3）
- [x] `backend/tests/e2e_modal_runner.py` — 非 done 终态直接 E2E FAIL 断言（探针命中）
- [x] `backend/pyproject.toml` + `uv.lock` — sqlalchemy/apscheduler 等未用依赖已删（探针=0），锁文件同步再生（删除行主体）

### 前端（2 项 · 已提交 `92c142a`）

- [x] `frontend/src/components/common/ConsolePane.tsx` — 快照 capRef 裁剪（replace 截断 + 追加 trim 双路径，行 100/108/114/149）。**修正上轮误报**：此前因工具输出损坏误判"未完成"，本轮实测已写入
- [x] `frontend/src/pages/volume/index.tsx` — 卷选择器跟随主卷名 `meta?.volume` effect（+8/−0）

## 二、待做项（✅ 2026-09-27 全部完成）

- [x] ~~`frontend/src/pages/models/index.tsx` 同步防双击~~ — `97c7571`：sync() 加 in-flight ref 守卫（原 disabled×8 为空列表态，非守卫）
- [x] ~~前端依赖清理：`zustand`~~ — `36d59db`：src 零 import 探针确认后移出 package.json，pnpm-lock 同步再生
- [x] ~~死代码：workflows 页未用导入、`flagLabel`~~ — `36d59db`：删 useState/Descriptions 未用导入与 flagLabel 函数
- [x] ~~README：部署一节补全 + e2e/ 目录描述修正~~ — `d456f49`
- [x] ~~CLI JSON 解析去重~~ — `36d59db`：modal_volume/client 新增 cli_json_list()，替换 4 处重复实现（行为不变，真实 CLI 输出冒烟验证）
- [x] ~~sync/ 空包~~ — 实测目录不存在，无需处理

## 三、收尾验证与提交步骤（✅ 2026-09-27 全部完成，记录如下）

1. [x] `npm run build` + `tsc --noEmit` — tsc 0 错误，vite 3704 模块构建通过（P2 前端补丁首道验证）
2. [x] 后端重启 + curl 冒烟 **5/5**（断言脚本 `tmp/smoke_assert.py`，退出码 0）：
   - `GET /api/v1/config` → volume=comfyui-data / task_timeout=3600 正确回读
   - `POST /api/v1/routing/preview` → final_backend=local，trace 6 步完整
   - `GET /object_info` → 200 合并 966 节点（**前提：先启动本地 ComfyUI**；未启动时 502 属 P2 显式超时路径的预期行为）
   - `GET /api/v1/requests` → total=19 / returned=19
   - `GET /api/v1/logs/modal` → online=False（Modal 未运行，真实可用性）
3. [x] 浏览器 E2E **4/4**（Vite 5173 + 后端 8189 + ComfyUI 8188；截图存 `tmp/gui-test-screenshots/`）：
   - 路由干跑 trace：逐规则求值 + default 兜底 + 最终决策提示，布局正常
   - 设置页 tab：**实为 6 个**（本地/启动命令/桥接/Modal/护栏与预算/设置同步）全部切换正常；secret 掩码 `as-aY****Ru1X` 符合红线（原记录"三个 tab"已过时）
   - 卷选择器跟随主卷名：云端直下/目录浏览两处均显示主卷 `comfyui-data` 并带"主卷"标签，目录浏览可读卷内目录
   - 控制台快照裁剪：155 行日志裁进 240px 内部滚动视口（内容实际高 3641px），页面不撑高；Modal 面板 0/0 行 + 通道未就绪为真实状态
4. [x] 单测回归 `uv run pytest tests/unit -q` → **18 passed**（4.05s），与基线一致
5. [x] 分批提交 P2：`b2a75d8` 后端+runner（10 文件）/ `92c142a` 前端（2 文件）/ `3f09472` 清理（pyproject+uv.lock）；diff 统计 +123/−276 与本清单一致，提交前凭证 grep 零命中

## 四、已撤销（勿改）

- runner/client.py "Modal 代理不透传 WS 帧"注释修正 — 该注释实际不存在，finding 撤回

## 五、红线（修复全程有效）

- 中继 API key 只存 `~/.comfybridge/config.toml` 或环境变量，绝不写入代码库、命令回显或提交
- ComfyUI workflow JSON 内含 api_key — 分享/入库前必须脱敏
- API 响应对凭证一律掩码（first5+`****`+last4）
- 验证只认：退出码、`py_compile`/`pytest` 摘要、短 ASCII 输出、断言脚本；长中文 heredoc 回显一律视为不可信

## 六、节点清单差异与节点同步（2026-09-28 实现 · 2026-09-30 补验真实卷同步）

### 变更范围

- `backend/app/api/v1/nodes.py` — 新增数据卷权威清单 `custom_nodes/.comfybridge-manifest.json`（`name/kind/repo/commit/has_requirements/enabled`）；`GET /api/v1/nodes/diff` 增加 `same|changed|missing_cloud|missing_local` 状态与云端 commit/启用状态；同步/移除后维护 manifest；旧卷无 manifest 时回退 volume 目录清单（schema 0）；支持停用节点（整目录按实际名落卷，`.disabled` 后缀由 ComfyUI 加载与依赖安装跳过）。2026-09-30 提交前修复：原"空 `.disabled` 标记目录"方案上传路径指向不存在的本地目录且 manifest 条目错位，改为停用内容整目录按实际名落卷（与 ComfyUI-Manager 停用约定一致），manifest 清理按实际条目名
- `frontend/src/api/nodes.ts` — `NodeDiffRow` 增加 `cloud_commit / cloud_enabled / state`
- `frontend/src/pages/nodes/index.tsx` — 差异表新增「一致性」列与 commit 两端对照；停用节点也可同步
- `modal-runner/runner.py` — `install_custom_node_deps` 跳过已停用节点（`.disabled` 目录）
- `backend/tests/unit/test_nodes.py` — manifest 结构（含停用包按实际名登记）与 `_safe_name` 边界用例

### 验证

- [x] 后端单测 `pytest tests/unit -q` → **29 passed**（基线 18 + 本轮新增）
- [x] 前端 `pnpm build`（`tsc --noEmit` + vite）→ 0 错，3706 模块构建通过
- [x] `py_compile`（`nodes.py` / `runner.py`）+ `git diff --check` → 通过
- [x] 隔离回放（假 Modal CLI + 临时目录）：`nodes_sync` 上传节点目录与 `requirements.txt`，写入 `.comfybridge-manifest.json`，manifest 条目 `enabled: true`
- [x] 真实 Modal 卷同步（2026-09-30 网络恢复后补验）：`POST /api/v1/nodes/sync` 实同步 2 项全 ok；卷上 `custom_nodes/.comfybridge-manifest.json` schema=1（pkg comfybridge-openai-image + 单文件 websocket_image_save.py，经 `modal volume get` 回读确认）；`GET /api/v1/nodes/diff` 两行 state=`same`，counts synced=2 / only_local=0 / only_cloud=0

### 待做 / 后续

- M2-2 镜像固化：`nodes.lock.yaml` + `install_nodes.sh` + `modal deploy` + 失败回滚
- 清单采集补全：ComfyUI-Manager API 兜底；差异结果持久化缓存供路由 `node_presence` 使用
- ComfyUI 版本对齐作业（M2-5 余项）

## 七、额度使用与监控（M4-1 部分 · 2026-09-30）

### 变更范围

- `backend/app/cost/__init__.py`（新）— 估算模型：GPU 单价（`[bridge] gpu_hourly_rate_usd`，默认 1.95）× 请求墙钟秒数（云端提交含冷启动等待 → 终态）；`budget_state`/`summary` 聚合与预算判定
- `backend/app/logs/store.py` — requests 表增 `gpu_seconds`/`est_cost_usd` 列（老库 PRAGMA 探测 + ALTER 迁移）；`record_cost` 按次入账；`cost_totals`/`cost_daily` 读取时 SQL 现算（规划原设计为 cost_daily 表 + 夜间重算，实现偏差：无调度器、读取即准确）
- `backend/app/runner/executor.py` — 计时起点挂 state（含冷启动等待，即真实计费窗口），`_finish` 终态入账（成功/失败/中断都计；提交未达不计），控制台打印单次估算
- `backend/app/routing/engine.py` — `decide()` 新增 budget 参数；超限在可用性之前求值（策略优先于健康），`budget_guardrail` 留痕，按 `on_budget_exceed` 回落本地/拒绝
- `backend/app/proxy/prompt_submit.py` — 决策前采集预算现状传入 decide；超限且决策目标为云端时 Modal 控制台告警（「并通知」）
- `backend/app/api/v1/cost.py`（新）— `GET /api/v1/cost/summary` · `PUT /api/v1/cost/budget`（增量更新 [bridge] 预算/超限动作/单价，落盘热生效，返回刷新后 summary）
- `backend/app/api/v1/config.py` — 抽出 `update_bridge_section` 供 cost API 复用同一落盘路径
- `frontend` — 仪表盘「额度与成本」卡片（近 14 天成本柱图 + 本月额度面板含预算进度条 + 预算编辑弹窗）与超限 Alert 横幅；顶栏「预算」角标接真实状态（原「规划中」占位退役）；设置页「护栏与预算」表单接线（原禁用占位转正式字段）；日志页请求表新增「成本」列
- `backend/tests/unit/test_cost.py`（新 8 用例）+ `test_routing_engine.py` 预算护栏 4 用例

### Modal 账单实时查询（同日追加：账户级真实消耗）

- 背景：本地估算只有"自设预算 vs 估算值"，看不到 Modal 账户真实消耗。调研结论：api.modal.com 为纯 gRPC；
  官方封装在 modal 1.5+ 有两处——CLI `modal billing summary/report/rates` 与 SDK `Workspace.billing.summary()`
  （CLI 即 SDK 的官方前端）；dashboard 用的 GraphQL 不收 API token；**无公开的「剩余积分总额」接口**
  （plan 总额仅仪表盘会话可见）。
- `backend/app/cost/modal_billing.py`（新）— 走 **CLI `modal billing summary --for YYYY-MM --json` 子进程**
  （与 volume/app 完全同一边界：`shutil.which("modal")` + `auth_env()` 注入凭证，未配置回落 ~/.modal.toml，
  后端主进程不 import modal SDK；曾用 modal_proto stub 直调 WorkspaceBillingSummary，已替换——proto 面是内部
  生成物，官方口径是 CLI/Workspace.billing）。成功缓存 120s / 失败 20s（前端 30s 轮询不连续拉子进程），
  超时 45s 强杀，CLI 报错剥 DeprecationWarning 噪音后透出，任何异常转 `ok=false + error` 展示。
  pyproject 依赖注释同步更新
- `cost/__init__.afull_summary` = summary() + modal 账单；`GET /api/v1/cost/summary` 与 `PUT /api/v1/cost/budget`
  均切换到它（账单失败不影响本地汇总，仅 `modal.error`）
- 前端 — 「本月额度」卡片新增 Modal 账单行（`Modal 账单（YYYY-MM-DD 起，UTC）$X` + 积分抵扣/实际计费，
  计费 > 0 标「赠金/额度已耗尽」，悬停显示分项与缓存说明；获取失败显示原因）；顶栏「预算」角标 tooltip 追加 Modal 本期消耗
- 展示口径：窗口 `[UTC 月初, +1 月)`（Modal 按 UTC 结算，与本地日界的估算聚合存在时区差异）；
  metered = Modal 计量（含 Deployed/Ephemeral Apps、Volumes 等，不止 GPU 跑图）；无剩余总额字段，以实际计费 > 0 表达耗尽

### 验证

- [x] 后端单测 `pytest tests/unit -q` → **46 passed**（41 + 账单 5：CLI JSON 解析 / 垃圾输出兜底 / 成功缓存 / 失败剥噪短缓存 / afull_summary 合并）
- [x] 前端 `npx tsc --noEmit` + `npm run build` → 0 错
- [x] API 冒烟：GET summary 默认关闭 → PUT budget $0.01 落盘 config.toml 回读正确 → 注入 900s/$0.4875 模拟请求 → `POST routing/preview`（manual=modal）命中 `budget_guardrail` 回落本地 → summary today/month 汇总正确 → 清理模拟数据与预算归零
- [x] Modal 账单真实验证：`modal billing summary --for 2026-09 --json`（注入桥接器凭证）与 `/api/v1/cost/summary` 均返回真实数据（2026-09 本期计量 $5.97 = deployed_apps 4.39 + ephemeral_apps 1.12 + volumes 0.47，积分抵扣 $5.50，实际计费 $0），二次调用命中缓存
- [x] 浏览器 E2E（Vite 5173 + 后端 8189；截图 `tmp/gui-test-screenshots/m4_1_*.png`）：仪表盘成本卡片与曲线 → 预算弹窗编辑保存 → 超限横幅 + 顶栏红标 + 红色进度条 → 设置页护栏表单真实值 → 日志页成本列，全部正常；追加本轮 Modal 账单行渲染验证（`m4_1_modal_billing_*.png`）

### 估算口径（有意从简，UI note 同步展示）

- 单价 × 请求墙钟秒数（含冷启动等待）；@modal.concurrent 复用容器时多请求墙钟重叠按请求直加 = 保守上限；scaledown 保活窗口无请求归属不计入；与 Modal 账单对账属 v2（规划 §8.3）

### 待做 / 后续

- 成本按工作流/模型维度聚合、CSV 导出（规划 §8.3 报表升级）
- 逐项账单对账（本地按次估算 vs Modal 账单明细，规划 §8.3 v2）；「剩余积分总额」待 Modal 公开接口后补
- 云端最大并发护栏（设置页占位仍在）

## 八、ComfyUI 版本对齐（M2-5 第一片 · 2026-09-30）

### 背景

- 实测发现版本漂移已真实发生：本地 ComfyUI Desktop 自动升级到 **v0.38.0（6b747c04）**，
  云端镜像 pin 仍是 runner.py 硬编码的 **v0.37.4**——正是规划 §1 要避免的 object_info/前端协议不匹配风险，
  且原链路（diff/deep/本地清单）没有任何环节做版本比对，漂移不可见。
- 方向确认：**本地为版本权威，云端追随本地**（与节点同步方向一致）。

### 变更范围

- `modal-runner/lock/comfyui_version.txt`（新）— 云端 ComfyUI 版本锁文件（当前 v0.37.4 起步，对齐后更新）
- `modal-runner/runner.py` — `COMFYUI_VERSION` 改为读锁文件 `_pinned_comfyui_version()`（缺失/为空回退
  `DEFAULT_COMFYUI_VERSION`）；原「本地升级后手改这里」注释退役
- `backend/app/nodes_mgr/`（新包）`version_align.py` — 版本对齐核心：
  `status()`（读本地 `comfyui_version.py` + git commit vs 锁文件 pin，纯文件读可轮询）、
  `start_align()`（写 pin → 子进程 `modal deploy modal-runner/runner.py` 后台任务，与 volume/billing 同边界：
  `shutil.which("modal")` + `auth_env()`，主进程不 import modal SDK）、
  失败（非零退出/CLI 缺失）自动回滚 pin；超时（1800s）属不确定态保留新 pin 并在错误信息说明；
  单任务互斥，状态存进程内存（重启即清空，与 comfy_scan 同口径）
- `backend/app/api/v1/nodes.py` — `GET /nodes/version`（对照 + 任务状态）、
  `POST /nodes/version/align`（409 任务进行中 / 400 未配置凭证或无本地版本）
- `frontend/src/api/nodes.ts` — `NodeVersionStatus/VersionJob` + `useNodeVersion`（任务 running 时 3s 轮询）+ `alignNodeVersion`
- `frontend/src/pages/nodes/index.tsx` — 页顶 `VersionBanner`（两 tab 共见）：漂移 → warning Alert
  「本地 vX @ commit · 云端 pin vY」+「对齐云端 → vX」按钮（Modal.confirm 说明重建时长/容器重启/数据卷不受影响）；
  running → info Alert；job 由 running 转 ok/error 时 message 提示并刷新
- `backend/tests/unit/test_version_align.py`（新 12 用例）— tag 归一化 / drift 判定 / 无本地版本 /
  pin 回退 / 对齐成功写 pin / pin 相同 noop / 并发 409 / deploy 失败回滚 / 成功保留 / 超时保留并标记不确定 / CLI 缺失回滚

### 验证

- [x] 单测 `pytest tests/unit -q` → **58 passed**（46 + 版本对齐 12）；ruff 新文件 0 错（nodes.py 4 处为基线原有，stash 对比确认未新增）
- [x] 前端 `npx tsc --noEmit` + `npm run build` → 0 错
- [x] API 冒烟：`GET /nodes/version` 真实检出漂移（local 0.38.0/6b747c04 vs pinned v0.37.4，drift=true）；
  `POST /nodes/version/align` → pin 写入 v0.38.0、任务 running、`modal deploy` 后台执行
- [x] **真实对齐闭环**：`modal deploy` 成功（82s，Modal 层缓存住 torch 等大头）；
  云端 `/system_stats` 冷启动实测 `comfyui_version: "0.38.0"`；`GET /nodes/version` → drift=false / pinned=v0.38.0 / job=ok
- [x] 浏览器 E2E：节点页正常渲染（对齐后 banner 正确隐藏）；临时把 pin 拨回 v0.37.4 验证
  漂移警告横幅（本地/云端对照 + 风险说明 + 「对齐云端 → v0.38.0」按钮）后即恢复 v0.38.0；
  截图 `tmp/gui-test-screenshots/m2_5_version_*.png`。期间发现 Vite dev server 随上一会话退出，已重启

### 待做 / 后续

- M2-2 镜像固化：`nodes.lock.yaml` + `install_nodes.sh`（节点依赖预装进镜像，消除每次冷启动 pip 安装）+ 失败回滚
- 版本对齐余项：对齐历史留痕；「对齐」与「节点依赖」合并为一次重建（M2-2 后自然合并）
- 节点同步其余遗留见 §六待做（changed 内容哈希检测、node_presence 路由、反向同步提示等）

## 九、M2-2 镜像固化（2026-10-01）

### 变更范围

- `backend/app/nodes_mgr/deploy.py`（新）— 共享 modal deploy 后台任务：单飞互斥
  （`begin_exclusive` 409）、失败回滚回调注入、超时（1800s）保留新文件并标记不确定、
  CLI 输出剥噪 tail。版本对齐与镜像固化共用一条部署通道，消除双 deploy 竞争
- `backend/app/nodes_mgr/scan.py`（新）— `scan_custom_nodes` 从 api/v1/nodes 抽出复用
- `backend/app/nodes_mgr/lockfile.py`（新）— `nodes.lock.yaml` 生成（本地扫描为权威：
  启用+有 requirements 的包，依赖去注释逐条登记 + comfyui_version 取 pin；JSON 文本
  = 合法 YAML 1.2 子集，不新增 pyyaml 依赖）+ `install_nodes.sh` 渲染（逐节点
  `pip install`，set -e 严格失败）+ 备份/回滚
- `modal-runner/runner.py` — 镜像末尾追加 `add_local_file(lock/install_nodes.sh, copy=True)`
  + `run_commands("bash …")`：依赖预装进镜像且脚本变更不失效 ComfyUI 基础层缓存；
  冷启动 pip 安装降级为兜底（覆盖卷模式新装/未固化节点）。**首次实测失败**：
  modal 1.5.x `add_local_file` 默认 copy=False（运行时挂载，构建期不可见），按 CLI
  提示改 `copy=True` 后成功——失败已被回滚机制兜住（锁/脚本自动还原）
- `backend/app/api/v1/nodes.py` — `POST /nodes/image/rebuild`（409 部署进行中）；
  `/nodes/version` 的 job 改为共享 deploy 状态（含 label）
- 前端 — 差异矩阵 alert 新增「固化到镜像」按钮（Popconfirm 说明语义）；
  顶部部署横幅改为显示共享 job 的 label（对齐/固化两种目的）
- `backend/tests/unit/test_version_align.py` 迁移到共享 deploy 补丁目标（8 用例）+
  `test_lockfile.py`（6 用例：requirements 解析/启用过滤/脚本渲染/JSON=YAML 往返/回滚）

### 验证

- [x] 单测 64 passed（+6 lockfile；版本对齐 8 用例迁移到共享 deploy 补丁目标）；ruff 新文件 0 错
- [x] 真实固化：锁文件生成 `comfybridge-openai-image: requests, pillow` + 脚本渲染正确；
  `POST /nodes/image/rebuild` 真机 deploy ok（copy=True 后）；手动 redeploy 输出确认
  `install_nodes.sh` 已挂镜像层；云端冷启动 `/system_stats` = 0.38.0
- [x] 浏览器：差异矩阵「固化到镜像」按钮渲染正常（截图 `m2_2_rebuild_button.png`）

### 已知行为 / 待做

- deploy 以子进程随后端存活：后端在 deploy 进行中被强杀会中断部署（云端状态不确定，
  与超时同语义）；部署任务状态存内存，重启清零
- 曾观测到 deploy 报 ok 后应用从 workspace 消失（endpoint invalid function call），
  手动 redeploy 恢复——疑似 deploy 收尾期后端被强杀所致；如复现按「重新固化/对齐」自愈
- M2-2 余项：失败回滚自动化已做，nodes.lock 与版本 pin 的合并重建入口仍分开（共用 deploy 通道）

## 十、M2-4 工作流双向同步（2026-10-01）

### 变更范围

- `backend/app/sync/workflows_rules.py`（新）— 三方检测纯逻辑：仅本地/仅云端/
  两端一致/基线判单边改/冲突（基线缺失或双边都改）；冲突副本命名 `x.conflict-<ts>.json`
- `backend/app/sync/workflows_sync.py`（新）— 本地 `user/default/workflows/`（hash）×
  数据卷同名目录（volume ls + 逐文件 get 计 hash，免容器）× last-synced 基线
  （存 `~/.comfybridge/workflows_state.json`，不污染 ComfyUI 工作流目录）；
  `status()` 读取时顺带刷新 synced 行基线；`apply()` 单飞互斥执行 push/pull/keep_both
  （keep_both = 云端版拉为本地冲突副本 + 本地版推上云端）；**v1 边界：删除不传播**
  （仅本地的文件会重新推回云端，防误清卷）
- `backend/app/api/v1/workflows.py` — `GET /workflows/sync`（矩阵）+ `POST /workflows/sync`（actions）
- `backend/app/modal_volume/client.py` — 修复 CLI quirk：`volume ls --json` 的 filename
  对非 ASCII **双重转义**（解析后仍是字面 `\u98ce`，中文文件名全部失效），
  `list_dir` 出口 `_unescape_cli_name` 还原（模型列表/卷浏览同样受益）；
  `volume get` 补 `--force`（pull 目标已存在时 CLI 默认拒绝覆盖）
- 前端 — 工作流页「同步状态」tab 从静态示例数据换为真实矩阵：文件/两端 hash/状态
  （仅本地、仅云端、本地已改、云端已改、冲突）/逐行动作（推送到云端、拉取到本地、
  冲突三选：推本地版|推云端版|保留双副本）+「一键同步」（冲突行除外）；「运行统计」
  保留示例并标注 v2
- `backend/tests/unit/test_workflows_sync.py`（11 用例：三方检测矩阵/无基线冲突/
  单向新增/基线刷新/apply 三动作/文件名校验/假卷回放）+ `test_modal_volume_client.py`（3 用例）

### 验证

- [x] 单测 **78 passed**（+11 workflows +3 volume quirk）；ruff 新文件 0 错（client.py UP041 为基线遗留）；`tsc` + `vite build` 0 错
- [x] 真实卷周期探针 `tmp/probe_wf_sync.py` **13/13**：本地改→local_changed→push→synced；
  只改云端→cloud_changed→pull（内容实拉回本地）→synced；双边改→conflict→keep_both
  （云端版留 `cb-sync-probe.conflict-<ts>.json`、本地版推上云端）→synced；探针已清理
- [x] 真实工作流上卷：2 个 krea2 工作流 push 后两端 hash 一致（`3c77ce08`/`01efc2c8`），
  状态 synced；中文名经 unescape 修复后正确显示
- [x] 浏览器：工作流页同步矩阵真实数据渲染（截图 `m2_4_workflow_sync_matrix.png`），
  节点页差异矩阵与固化按钮正常（`m2_2_rebuild_button.png`）

### 待做 / 后续

- 冲突的「双列对比视图」（原占位按钮的另一半）与运行统计聚合（v2 报表）
- 删除传播策略（本地删/云端删的双向 tombstone）——v1 有意不做，防误删
- M2 全景收尾：node_presence 路由规则（M3 表内）与反向同步提示仍待做

## 十一、Runner 端点鉴权（P0 安全 · 2026-10-01）

### 背景
- 云端 `web_server` 端点此前零鉴权：任何拿到 web_url 的人可直接提交工作流/取图，烧 GPU 额度。

### 改动
- `modal-runner/runner.py`：`@modal.web_server(..., requires_proxy_auth=True)` —— 无凭证请求被
  Modal 边缘代理 401（不触发容器，烧不到 GPU）。真机 redeploy 后无凭证/错凭证均 401 实证。
- `backend/app/core/config.py`：ModalConfig 新增 `proxy_token_id`（wk-…）/`proxy_token_secret`（ws-…）。
  **关键发现 1：代理鉴权 token 是独立凭证族**（wk-…/ws-…，仅仪表盘 modal.com/settings/proxy-auth-tokens 可建，
  CLI 无命令），与账户 API token（ak-/as-）不通——两种 ak/as 凭证带 Modal-Key/Secret 头均被
  边缘代理判 "invalid credentials"（探针实证）。
- `backend/app/modal_volume/client.py`：`proxy_credentials()` 读代理凭证对（strip；未配置返回 None；
  不回显不落日志）。HTTP 无 CLI 那条「缺省回落 ~/.modal.toml」路径（且 toml 里也没有 wk- 族），不设兜底。
- `backend/app/runner/client.py`：`proxy_headers()`（Modal-Key/Modal-Secret）；`_client()` 默认注入全部
  六处云端 HTTP 调用；`available()` 加代理凭证门控（缺凭证时通道判不可用，避免决策后必然 401）。
- `backend/app/runner/executor.py` + `backend/tests/ws_probe.py`：云端 WS 连接带
  `additional_headers=proxy_headers()`（websockets>=14 新实现参数名，装 17.1），
  并 `compression=None`（见关键发现 2）。
- `backend/app/api/v1/modal.py`：warmup 直连 GET 带鉴权头。
- `backend/app/api/v1/config.py`：`_modal_view`/`_merge_modal` 扩展两个新字段
  （secret 脱敏 first5+****+last4；PUT 留空=保留、null=清除，与 token_secret 同语义）。
- `backend/app/api/v1/health.py`：缺代理凭证时 reason 精确提示（不再笼统「通道不可用」）。
- `frontend/src/api/config.ts` + `pages/settings/index.tsx`：Modal tab 新增代理凭证两个字段
  （Password 输入、留空不修改提示、清除凭证按钮同时清代理凭证）。
- `backend/config.example.toml`：字段说明补全。

### 验证
- 单测 `uv run pytest tests/unit -q` → **86 passed**（新增 test_runner_auth.py 8 例：凭证读取/头注入/
  available 门控/websockets 参数兼容；ak-/as- 族不得充当代理凭证有专属用例）。
- ruff（改动文件）与 stash 基线完全一致，零新增；py_compile 通过；tsc 0 错 + vite build 6.8s。
- 真机探针（tmp/probe_runner_auth.py + tmp/probe_401_reason.py）：无凭证 401 / 错 secret 401 /
  ak-as 凭证 401 "invalid credentials"（`requires_proxy_auth` 生效实证）。
  **关键发现 2：Modal 边缘不支持 RFC 7692（permessage-deflate）**——websockets 默认提供压缩扩展，
  升级握手成功后连接立即被远端 1000 干净关闭、status 事件收不到（升级已过鉴权，纯 HTTP 一切正常）；
  `websockets.connect(..., compression=None)` 后连接稳定、事件正常（实测 30s 只听不掉线）。
  查询串带凭证（`modal_key`/`modal_secret` 与 `Modal-Key`/`Modal-Secret` 两种命名）均 401——WS 只认请求头。
- 凭证经用户仪表盘创建、经设置 API 写入 config.toml 后完整探针 **PASS**：
  无凭证 401 / 错 secret 401 / 正确凭证 200 / WS 握手 + status 事件推送，
  桥接器 health `online: true`（Runner 通道就绪，云端模型索引 8 项）；
  executor 加 compression=None 后单测 8 passed、后端重启加载生效。

## 十二、M3-1 路由规则 CRUD（2026-10-01）

### 语义（规划文档 §3.1 的数据化落地）
- 固定链不参与 CRUD：`manual → tag → cloud_model → default`（显式指定/安全/兜底）。
- 自定义链 `bridge.rules`：**空列表 = 完全沿用内置 gen_type/size 规则（历史行为零迁移）**；
  非空时插在 cloud_model 与 default 之间按序求值、首条命中生效，并替换内置那两条。
- 规则类型（`routing.engine.RULE_KINDS`）：`workflow_tag`（工作流名前缀，不区分大小写）、
  `gen_type`（video/image）、`min_pixels`（图最大边阈值）。校验在 API 层（validate_rules，
  非法 422 整链不落）；引擎对未知 kind/坏参数一律跳过并留痕（手编 config.toml 不致命）。

### 改动
- `backend/app/core/config.py`：`RoutingRule` 宽松模型 + `BridgeConfig.rules`。
- `backend/app/routing/engine.py`：RULE_KINDS 注册表 + validate_rules + decide() 自定义链求值
  （trace 行 `custom:<名称或类型>`，停用/未知类型均有留痕步骤）。
- `backend/app/api/v1/routing.py`：`GET/PUT /api/v1/routing/rules`（集合级整链替换；
  GET 附 kinds 注册表与按现行内置设置的种子建议）。落盘复用 config.update_bridge_section。
- `backend/app/api/v1/config.py`：**修复 `_dump_toml` 存量 bug**——list 内的 dict 元素被
  toml_str 当字符串转义（Python repr 落盘），回读即 ValidationError 毒化整个配置；改为递归
  render（dict → TOML 内联表）。此 bug 此前未触发只因列表里只存过字符串。
- `frontend/src/api/routing.ts` + `pages/routing/index.tsx`：路由页左侧改为「固定规则表 +
  可编辑自定义链」（启用开关/名称/类型/动态条件参数/目标/上移下移删除/保存）；
  空链时显示内置两条并附「按当前内置设置生成」种子按钮；干跑预览器不变。

### 验证
- 单测 `uv run pytest tests/unit -q` → **96 passed**（新增 test_routing_rules.py 10 例：
  校验器形状矩阵、空链兼容内置链、首条命中、停用/未知类型跳过、min_pixels 阈值边界、
  default 兜底、cloud_model 先于自定义链、_dump_toml 规则列表回环）。
- ruff 改动文件与 stash 基线一致（engine.py 仅存量 SIM114）；tsc 0 错 + vite build 5.9s。
- API 冒烟（真机 8189）：PUT 一条 workflow_tag 链 → GET 回读一致 → 干跑 `gpu:*` 命中
  `custom:显卡工作流上云` 决策 modal、普通名走 default → 非法 kind 422 → 恢复空链。
- 浏览器 E2E：路由页编辑器渲染、按内置设置生成、保存成功提示、贴 VHS 视频图干跑 trace 出现
  `custom:视频上云` 命中 + 最终决策云端（截图 `tmp/gui-test-screenshots/m3_1_routing_rules_editor.png`）；
  测试后删行保存恢复空链（GET 确认 0 条）。
- 期间事故与自愈：首次 PUT 触发上述 `_dump_toml` bug，坏行写进 config.toml 致后端整体 500；
  修复渲染器 + 手工移除坏行 + 重启恢复，并补回环单测防复发。
- 待办（M3 余项）：node_presence 路由规则（需本地/云端节点 class 差异的持久化缓存）。

## 十三、M3-2 node_presence 路由规则（2026-10-01）

### 语义
- 两种无参规则类型：`node_missing_local`（图引用了本地没有的节点 class，目标通常=云端）、
  `node_missing_cloud`（引用了云端没有的 class，目标通常=本地）。判定数据 = 工作流 class_types
  与两端在场缓存的差集；缓存未就绪（空集合）宁可跳过并留痕，不误判。

### 改动
- `backend/app/models_mgr/node_presence.py`（新）：本地/云端 /object_info class 集合缓存。
  15 分钟 TTL + kick 后台单飞；持久化 `~/.comfybridge/node_presence.json`（重启不丢，
  首次 snapshot 同步读盘一次）；时间戳用 wall clock（跨进程新鲜度成立）；单端失败保留
  另一端与旧值（实测 303 瞬时故障即走此路径恢复）。本地侧 20s 超时，云端侧走
  runner_client（660s 覆盖冷启动）。
- `backend/app/routing/engine.py`：RULE_KINDS 增两种类型（param_key 空 = 无参规则）；
  decide() 新增 presence 参数与两个判定分支（缺失 class 最多展示 3 个）。
- `backend/app/api/v1/nodes.py`：`POST /nodes/presence/refresh` 强制刷新（装完节点后手动保鲜）。
- `backend/app/proxy/prompt_submit.py`：决策前 kick 保鲜 + 传 presence 快照。
- `backend/app/api/v1/routing.py`：preview 传 presence；响应新增 presence 透明度字段
  （ready/local/cloud/error）。
- `backend/app/main.py`：lifespan 启动即后台预热 presence（与 cloud_index 同款）。
- `frontend/src/pages/routing/index.tsx`：无参类型条件参数列渲染说明文案；干跑事实行展示
  节点缓存规模与就绪状态；布局修复（左栏 13→16、tableLayout=fixed、列宽收紧——无参文案
  曾把条件参数列挤成竖排一字列）。

### 验证
- 单测 `uv run pytest tests/unit -q` → **104 passed**（新增 test_node_presence.py 7 例：
  双端取数+落盘回环、单端失败保留旧集合、TTL 命中不发请求、缺文件空快照、校验器接纳
  无参类型、引擎命中/未就绪跳过/云端缺节点留本地）。
- ruff 改动文件与 stash 基线一致；tsc 0 错 + vite build 6.8s。
- 真机四连（8189，本地 ComfyUI 0.38.0 已启动 + 云端 0.38.0 在线，两端各 966 class）：
  本地缺 class 命中 → modal；云端缺 class 命中 → local；两端都有 → 不命中走 default；
  恢复空链确认 0 条。
- 浏览器 E2E：UI 添加「本地缺节点」规则保存 → 干跑 trace 出现 `custom:node_missing_local`
  「本地缺失 class：BridgeMadeUpNode」→ 最终决策云端；事实行显示「节点缓存 本地 966 / 云端
  966 class」（截图 `tmp/gui-test-screenshots/m3_2_node_presence_rule.png`）；测试后恢复空链。
- 运维注意：后端启动时若本地 ComfyUI 未运行，local 侧记错保留空集，等 TTL 或调
  `POST /nodes/presence/refresh`（本地起来后）补齐——实测过该路径。

## 十四、云端最大并发护栏（M4-1 收尾 · 2026-10-01）

### 语义
- `bridge.max_cloud_concurrent`（默认 0 = 不限制）：在途云端任务（final_backend=modal 且
  queued/running）达到上限时，决策目标 modal 的请求按既有「云端不可用动作」
  （on_modal_unavailable）回落本地或拒绝；trace 行 `concurrency_guardrail`。
- 求值顺序：预算护栏 → 并发护栏 → 通道可用性（超预算优先于满载，均优先于健康检查）。
- 计数源 `store.count_active_modal`：用 final_backend 而非 backend（回退请求不占云端槽位）。
  保守护栏——决策计数与入队落库之间无事务，±1 竞态可容忍（真机验证走干跑呈现）。

### 改动
- `backend/app/logs/store.py`：`count_active_modal()` + `acount_active_modal()`。
- `backend/app/core/config.py`：`BridgeConfig.max_cloud_concurrent`。
- `backend/app/routing/engine.py`：decide() 新增 `cloud_active` 参数与护栏分支。
- `backend/app/proxy/prompt_submit.py` / `api/v1/routing.py`：决策前计数传入
  （本请求尚未入库，不含自身）；preview 响应新增 `cloud_active`。
- `frontend/src/api/config.ts` + `pages/settings/index.tsx`：护栏 tab 占位字段转正
  （0=不限制 InputNumber，接 saveGuard 真实保存），说明文案更新。

### 验证
- 单测 `uv run pytest tests/unit -q` → **109 passed**（新增 5 例：超限回落/拒绝、
  未达限与 0=关闭与未采集均不触发、预算护栏优先级；store 计数只认 final_backend=modal
  非终态——回退/本地/终态行均排除；存量 test_routing_engine 的 _Bridge 桩补新字段）。
- ruff 改动文件与 stash 基线一致（14 个全存量）；tsc 0 错 + vite build 8.1s。
- 真机闭环（8189）：设上限 1 → 伪造一条 queued 云端任务 → 干跑 manual=modal 出现
  `concurrency_guardrail`（"云端在途任务 1 已达并发上限 1"）且 final=local → 任务置 cancelled
  后护栏解除 → 配置恢复 0。注意：护栏只对决策目标为 modal 的请求生效（默认 local 的
  请求本就不上云，第一轮探针未触发属预期）。
- 浏览器：设置 → 护栏与预算 tab 字段/说明/顶部提示三处渲染确认（value=0 恢复态）。

## 十五、打包分发：wheel 单包 + GUI 嵌入托管（M4 · 2026-10-01）

### 语义
- 产物 `backend/dist/comfy_bridge-*.whl`：一键 `cd backend && uv run python scripts/build_package.py`
  （前端嵌入构建 → modal-runner 复制进 pkg_assets → uv build → wheel 内容自检，缺关键文件即失败）。
- 安装即用：`pipx install <wheel>`，入口脚本 `comfy-bridge [--host --port]`（app/cli.py）。
- 管理面 GUI 挂 `/bridge`（app/gui_static：/bridge/assets 走 StaticFiles，其余 /bridge/* 一律回退
  index.html；防穿越只回源产物目录内真实文件）；`/` 与其余路径仍是 ComfyUI 数据面透传，
  管理面不侵占数据面根路径。产物目录缺失（开发态常态）自动跳过挂载。
- 前端 `build:embed`：`vite build --base=/bridge/ --outDir ../backend/app/static --emptyOutDir`；
  router basename = import.meta.env.BASE_URL —— dev（/）与嵌入构建（/bridge/）同一份配置。
- modal-runner 双布局解析（app/runner_assets.py）：仓库 modal-runner/ 优先，wheel 安装回落包内
  app/_modal_runner/ 副本；nodes_mgr.deploy/lockfile/version_align、volume_mgr.downloads/moves
  五处 parents[N] 硬编码全部收口（安装态 parents[3] 会指到 site-packages 之外）。
- 打包配置：backend/pyproject.toml 接 hatchling（发行名 comfy-bridge，wheel 顶层包沿用 `app`，
  pipx 每应用独立 venv，通用名无冲突面）；static 产物经 `[tool.hatch.build] artifacts` 放行
  （gitignore 文件默认不进构建）；`[tool.uv] package=false` 保持 dev 免构建——否则 uv run 会
  因 force-include 缺 pkg_assets 而直接失败。

### 改动
- 新增 `backend/app/gui_static.py`、`backend/app/runner_assets.py`、`backend/app/cli.py`、
  `backend/scripts/build_package.py`、`frontend/src/vite-env.d.ts`（import.meta.env 类型声明）。
- 修改 main.py（mount_gui 于 catch-all 之前）、五处 runner 路径常量、pyproject（build-system +
  project.scripts + hatch + python-socks）、frontend package.json / router.tsx、scripts/build.ps1
  （改用 pnpm build:embed）、.gitignore（backend/pkg_assets/）。

### 关键发现
- **modal 1.6+ 系统代理硬依赖**：客户端检测到系统代理即要求 `python-socks`（modal[api-proxy-support]），
  缺失直接 "Could not connect to the Modal server"，1.5.5 无此要求。分发包必须开箱可用 →
  直接声明 `python-socks>=2.0`；装上后 1.6.0 经系统代理连接正常（真机 app list --json 验证 OK）。
- hatchling 默认不收 gitignore 文件进 wheel/sdist，嵌入产物必须 artifacts 显式放行；
  force-include 源必须在工程根内（modal-runner 在 backend/ 之外，故打包脚本先复制进来）。

### 验证
- 单测 `uv run pytest tests/unit -q` → **117 passed**（新增 8：gui_static 无产物跳过 / SPA 回退 /
  根文件直出 / 资产缺失 404 / 路径穿越防护；runner_assets 仓库优先 / 包内回落 / dev 默认解析）。
- 真实打包冒烟：干净 venv pip 安装 wheel → `comfy-bridge --port 8199`：/bridge/ 与 /bridge/settings
  深链接 200（SPA 回退生效）、JS 资产 200（2.5MB，antd 为 CSS-in-JS 无独立 CSS 属预期）、
  /api/v1/health 200 且 modal online=True、`/` 数据面透传 ComfyUI 200、穿越请求 404；
  安装态 `_modal_runner` 副本五处路径 is_file() 全真。
- 浏览器验收（:8199/bridge/）：仪表盘双通道在线（本地 0.38.0 · Modal 已接入 · 通道就绪 · 账单可见）、
  设置页深链接渲染正常；截图归档 tmp/gui-test-screenshots/pkg_bridge_{dashboard,settings}.png
  （visual-judge 供应商本环境不可用，按约定人工查验：布局/字体/组件无用户可感知问题）。
- dev 无回归：重启 8189 后 /bridge/、健康检查（local+modal 双在线）、`/` 数据面全通；vite :5173
  原路径不受 basename 改动影响（dev BASE_URL=/）。
- ruff：改动集 6 条全为 downloads/moves 存量基线（UP037/UP041/S110）；全量 68 vs stash 基线 69
  （净 -1，无新增）。

## 十六、成本报表 v2（M4-1 · 2026-10-01）

### 语义
- 「成本报表」页（/cost，导航新增）：①成本聚合——按工作流 / 主模型两维度汇总近 30 天已入账云端请求
  （金额降序 + 占比条）；②账单对账——本地按次估算 vs Modal `billing report` 官方明细（GPU 资源计量同口径
  对比 + 全资源计量），UTC 月窗口（月份选择器）+ UTC 日界逐日双列；③CSV 导出——逐条流水 / 按工作流 / 按主模型
  （utf-8-sig，Excel 直开）。仪表盘「额度与成本」卡新增「成本报表」入口链接。
- 主模型口径 = 图内 checkpoint/扩散模型权重引用（ckpt_name 优先，其次 unet_name，都没有退模型引用清单首个）；
  新请求提交时落库 requests.model_name，历史请求启动时后台从图快照回填（快照缺失/无可识别模型写空串哨兵，
  聚合展示「未记录」/「未命名」，不重复扫描）。

### 改动
- 新增 `backend/app/cost/reconciliation.py`（UTC 月界换算 + 本地/Modal 逐日合并 + 比值）、
  `frontend/src/pages/cost/index.tsx`；修改 store.py（model_name 列 + 迁移 + cost_breakdown/cost_entries/
  cost_daily_utc/requests_missing_model/set_model_name）、routing/engine.py（extract_primary_model）、
  prompt_submit.py（落库 model_name）、cost/__init__.py（backfill_model_names + GPU_TYPE 上移 constants）、
  modal_billing.py（report()：CLI `billing report --start --end --show-resources --json` + 解析聚合 + 独立缓存）、
  api/v1/cost.py（/report /reconciliation /export）、main.py（启动回填任务）、前端 api/cost.ts + router + Shell 导航 +
  dashboard 入口、core/constants.py（GPU_TYPE 共用）、README。

### 关键发现
- `modal billing report --show-resources --json` 按应用×日×资源（L40S/Memory/CPU…）给真实计量，是逐项对账的
  官方数据源；GPU 资源行与本地估算同口径（估算不含 CPU/内存），全资源对比用于展示差额来源。日界为 UTC
  （--tz 仅小时分辨率支持），与控制台逐日图（本地日界）最多差数小时，区间总量不受影响。
- 本地账本只记「经桥接且已入账」的请求；Modal 计量含一切容器时间（冷启动/保活/未跟踪直调），
  对账比值（Modal GPU ÷ 本地估算）短期大幅偏离属正常，持续偏离才说明 gpu_hourly_rate_usd 需校准。

### 验证
- 单测 `uv run pytest tests/unit -q` → **130 passed**（+13：聚合两维度/窗口外剔除、流水行、UTC 日界归桶、
  cycle 校验、对账合并两侧/Modal 失败仅本地、report 解析聚合/空区间、CLI 拉取缓存与参数、主模型回填含哨兵、
  extract_primary_model 三态；既有用例适配 insert_request 新参数）。
- ruff：全量 68 = stash 基线 68（改动集新增 0，新增代码 4 条 DTZ005 已改显式 UTC 消除）。
- `tsc --noEmit` 0 错；`pnpm build:embed` 嵌入构建成功（2.67MB JS）。
- 真机打穿：dev 8189 重启回填 16 条历史主模型；回放历史快照发起真实云端请求（首条云端侧丢失，
  重启重试后 done：74.9 GPU 秒、估算 $0.04058、model_name=krea2_turbo_fp8_scaled.safetensors 自动落库）；
  /cost/report 两维度真数据、/cost/reconciliation 本地 $0.0406 vs Modal GPU $1.5891（含此前会话全天计量，
  比值 39.16 属口径差异）、/cost/export CSV（BOM + 中文表头 + 附件头）全通过。
- 浏览器验收（:8189/bridge/cost 嵌入构建）：导航「成本报表」、聚合卡（工作流↔主模型切换、占比条、导出图标）、
  对账卡（月份选择器、四指标、资源构成标签、UTC 逐日双列表、口径注释）渲染全部正常；
  截图归档 tmp/gui-test-screenshots/cost_report_{workflow,model}.png（visual-judge 本环境不可用，
  按约定人工查验通过）。仪表盘卡片入口链接在位。

## 十七、M2 收尾（2026-10-01）

### 语义
- **单文件节点内容哈希检测**：节点 diff 对两端都在的单文件节点现算内容 sha256（本地直读、云端 volume get 到
  临时目录），元数据一致但内容不同 → changed；哈希不可得（拉取失败）退回元数据对比不误报。包级仍按
  commit/启用/形态判定（目录树哈希成本高，commit 已覆盖 git 包）。diff 行新增 local_hash/cloud_hash（12 位）
  与 cloud_repo。
- **反向同步提示**：云端独有节点（missing_local）在差异矩阵顶部出提示条——git 包给 `git clone <repo>` 可复制
  命令（含云端 commit），单文件给数据卷导出命令；只提示不自动改本地环境（红线）。
- **冲突双列对比**：工作流同步矩阵冲突行新增「对比」→ 双列弹窗（本地版/云端版）：大小、hash（12 位）、
  mtime（云端经卷取回无 mtime）、节点类型计数（仅该端有的类型橙色高亮）、模型引用清单。后端
  `GET /api/v1/workflows/compare` 现读两端文件解析摘要（UI 格式 nodes/widgets_values 与 API 格式 class_type/
  inputs 通吃）。
- **对齐/固化入口合并**：`POST /nodes/image/rebuild` 接受 `align_version`（前端固定 true）——固化节点依赖 +
  版本 pin 对齐一次 modal deploy 完成（此前两次，还互斥只能排队）；begin_exclusive 成功后才落盘
  （409 时零变更），回滚同时还原锁/脚本与 pin；本地版本缺失只跳过对齐不阻塞固化；pin 已一致则纯固化。
  节点页按钮改为「重建镜像」，版本漂移横幅的对齐动作也走同一入口（纯对齐端点 version/align 保留）。

### 关键发现
- M2-4 工作流同步的变更检测**本来就是内容 sha256**（两端现算 + last-synced 基线），早前 FIXLIST/文档把
  「changed 内容哈希检测」挂在工作流下是口径误记——真实缺口在**节点 diff 的单文件**（无 commit 可比），
  本次补上；规划/计划书相应段落已更正（mtime+size → sha256）。
- `/nodes/cloud/remove` 顺手修复：.disabled 变体多数不存在，`volume rm` 报 "No such file or directory"
  曾导致整个 remove 返回失败（文件其实已删）；现在缺文件不算失败。

### 改动
- 后端：api/v1/nodes.py（_local_file_hash/_cloud_file_hash/_diff_state 纯函数 + diff 行字段 + rebuild body +
  remove 容错）、api/v1/workflows.py（/compare + _summarize_workflow/_side_meta）、nodes_mgr/image_rebuild.py
  （align_version 合并重建）。
- 前端：api/nodes.ts、api/workflows.ts、pages/nodes/index.tsx（合并按钮/提示条/commit+hash 列）、
  pages/workflows/index.tsx（CompareModal + SideCard）。
- 文档：规划/计划书（检测口径更正 + M2 收尾标注）、README 余项收敛。

### 验证
- 单测 **141 passed**（+11：_diff_state 全分支含哈希漂移/不可得退元数据、合并重建写 pin/无漂移不写/
  非对齐不触碰版本函数/409 零落盘 + 双回滚、摘要双格式/坏 JSON、compare 本地侧/422/卷取回云端侧）。
- ruff 全量 68 = 基线 68（新增 0）；`tsc --noEmit` 0 错；`pnpm build:embed` 成功。
- 真机验证（dev 8189）：diff 单文件 websocket_image_save.py 两端哈希一致 → same；上传真实 zz-rev-demo.py
  到卷 → only_cloud=1、提示条给导出命令；构造真实冲突（本地改 + 卷 put 覆盖）→ conflict 行 + compare
  返回两端不同摘要（本地 3 节点 sd_xl / 云端 2 节点 juggernaut）。浏览器截图归档
  tmp/gui-test-screenshots/m2_{conflict_compare,nodes_reverse_hash}.png（人工查验通过）。
- 演示产物已清理（卷上 zz-rev-demo.py / 两端 zz-conflict-demo.json），两端恢复 synced 2/2。
- 备注：本地 ComfyUI（Desktop）本段验证期间未运行（顶栏显示未连接，不影响上述功能验证）；
  其嵌入 python 无法直接命令行拉起（缺 torch 路径），需经 Desktop 启动。

## 十八、零星项收尾：运行统计真实化 + 容器面板 + 首次配置向导（2026-10-02）

### 语义
- **运行统计真实化（M2-4 余项「远端运行统计回写」）**：工作流页「运行统计」Tab 此前是写死的示例数据；
  现改为 requests 表按工作流聚合——运行次数/成功/失败/本地·云端拆分/平均与最大耗时/估算成本/最近运行，
  近 7/30/90 天 Segmented 切换。**「回写」以 SQL 现算实现**（遵循成本口径 ADR：不做回写表/夜间任务，
  与成本报表同口径：未命名落「（未命名）」桶、估算成本只计云端入账请求）。
- **容器数面板（M4-2 余项）**：`GET /api/v1/launch/modal/containers`（modal container list --json，
  归属判定与停止逻辑共用 _container_rows；45s 进程内 TTL 缓存，停止容器后立即失效）。设置 → 启动命令
  的 Modal 控制条显示「运行中容器 N」+ 刷新按钮（悬停见容器 ID/启动时间）；仪表盘 Modal 通道卡片
  副标追加「容器 N 个运行中」。预热/停止操作后自动刷新计数。
- **首次配置向导（M4-3 余项）**：`ConfigWizard` 五步（检测 → Modal 凭证 → 本地 ComfyUI → 云端 Runner →
  完成），全部可跳过、复用设置页同款 PUT /config 热生效保存与 validate-comfy 校验。Shell 在「Modal
  凭证未配置且本浏览器未完成过向导」时自动弹一次（localStorage cb_wizard_done）+ 顶部常驻提示条
  （可「不再提示」）；设置页右上角「配置向导」按钮可随时重跑。完成步汇总三态并可直接预热云端容器。
  config 视图补回显 runner_enabled/web_url（非敏感；web_url 此前已由 /health 明文返回）。

### 关键发现
- `duration_ms` **不是表列**——store 读取时由 finished_ms − started_ms 现算；聚合 SQL 必须用
  `MAX(CASE WHEN finished_ms IS NOT NULL AND started_ms IS NOT NULL THEN finished_ms - started_ms END)`，
  直接 AVG(duration_ms) 会因列不存在而报错（单测覆盖）。
- 真机撞上 Modal CLI 网络抖动：`modal container list` 从平时 1.8s 飙到 43s（CLI 内部
  "Could not connect to the Modal server" 重试）。TTL 缓存 + to_thread 下事件循环不阻塞、面板显示
  「—」降级，属可接受行为；缓存焐热后前端即恢复正常显示真实计数（期间容器恰好缩容 1 → 0，数据真实）。
- 凭证红线自查：向导检测页初版把真实 Token ID（ak-…）明文渲染进截图，已改为只显示脱敏 secret
  （与设置页可编辑输入框的既有回显不同，向导是纯展示场景，无必要明文）；含凭证截图已删除重拍。

### 改动
- 后端：logs/store.py（workflow_run_stats + aworkflow_run_stats）、api/v1/workflows.py（GET
  /workflows/run-stats，days 1~365 / limit 1~200 校验，端点层补 local_runs）、api/v1/launch.py
  （_container_rows 抽取共用 + _list_modal_containers 45s TTL 缓存 + GET /modal/containers +
  停止后失效缓存）、api/v1/config.py（_modal_view 补 runner_enabled/web_url）。
- 前端：api/workflows.ts（WfRunStat/useWorkflowRunStats）、api/launch.ts（ModalContainers/
  useModalContainers）、api/config.ts（DTO 补 web_url/runner_enabled）、pages/workflows/index.tsx
  （运行统计真实化）、pages/settings/LaunchArgsPanel.tsx（容器数 Tag + 刷新）、pages/dashboard/index.tsx
  （容器数副标）、pages/settings/ConfigWizard.tsx（新）、components/layout/Shell.tsx（自动弹出 +
  提示条）、pages/settings/index.tsx（向导入口）。
- 测试：tests/unit/test_leftover_polish.py（9 个）。

### 验证
- 单测 **150 passed**（+9：聚合桶/时长 AVG 忽略 NULL/天数窗口与降序/成本只计云端、端点拆分与
  422、容器归属过滤/TTL 缓存命中/空与 CLI 失败/停止后缓存失效、config 视图字段与 secret 不回显）。
- ruff 全量 68 = 基线 68（新增 0）；`tsc --noEmit` 0 错；`pnpm build:embed` 成功。
- 真机验证（dev 8189 重启后）：`/workflows/run-stats?days=90` 返回真实聚合（23 次/成功 10/云端 12/
  平均 48.5s/$0.04058）；`/launch/modal/containers` 真实返回（先 1 后 0，容器缩容）；config 视图含
  web_url/runner_enabled。GUI：运行统计 Tab 真实渲染（截图 m3_runstats_tab.png）、启动命令容器面板
  （m3_modal_containers.png，运行中容器 0 + 刷新按钮）、仪表盘「已部署 · 云端模型 8 项 · 容器 0 个
  运行中」（m3_dashboard_containers.png）、向导全流程走查（检测三项就绪 → 凭证表单 secret 脱敏回显 →
  Runner 步部署指引与预填正确 → 完成步汇总，完成后 localStorage 标记落盘、弹窗干净关闭；截图
  m3_wizard_step0/step1_creds.png，step3/4 因截图表面间歇超时以 DOM 断言验证）。已配置环境下顶部
  提示条不出现（符合预期）；自动弹出路径需清空凭证才能触发，未做破坏性验证。

## 十九、提交前预校验框架（M3-5 余项 · 2026-10-02）

### 语义
- 提交 /prompt 前按**目标端**复刻 validate_prompt 的关键检查，把「云端跑到一半才发现缺节点/缺模型」
  提前到执行前拦截（零执行零费用）。数据源全部复用路由决策的缓存，模块自身不做索引 IO：
  节点 class 用 node_presence 两端 /object_info 在场缓存，模型文件用 local_index + cloud_index。
- 供给策略 `bridge.preflight`（设置 → 桥接）：**off** 不校验（默认，历史行为不变）；**strict** 拦截不带选项；
  **ask** 拦截并附结构化可操作选项（装哪些节点 / 上传哪些模型 / 改走云端）；**auto** 缺失模型全部可本地供给时
  先自动 `modal volume put` 上传云端卷再放行（即使同时缺节点——上传成功后剩余问题按 ask 语义拦截）。
  未知取值按 off；presence 索引不可用时节点校验降级跳过（note 说明），不误杀。
- 拦截响应 409：error 人类文案（ComfyUI 前端原样展示）+ options（strict 不带）+ rule_trace；
  请求落库为独立状态 **preflight**（预校验拦截，volcano 标签 + 日志筛选项），与「待云端通道」(blocked) 区分。
  auto_supply 上传清单/失败明细写 rule_trace + 控制台留痕。

### 关键发现
- **node_presence 缓存键是 "local"/"cloud"，后端名是 "local"/"modal"**——直接用 target 查 snapshot 会永远
  拿到空集、节点校验被静默跳过（单测抓出）；scan 内部做 side 映射。
- auto 初版把「缺节点」挡在供给之前；改为先完成可供给上传、再对剩余不可修复项拦截更符合使用顺序
  （修节点与传模型可并行准备，重提交时模型已就位）。
- 真机时序坑：本地模型索引 60s TTL——刚落盘的新模型文件最多延迟 1 分钟才进入 supplyable 判定。

### 改动
- 后端：proxy/precheck.py（新：extract_ref_categories/scan/decide_action/mark_supply_done/
  mark_supply_failures/block_options/blocked_message/auto_supply）、proxy/prompt_submit.py
  （预校验接线：决策后扫描 → 策略判定 → auto 供给 → 拦截落库与 409，local_names 复用）、
  core/config.py（bridge.preflight）。
- 前端：api/config.ts（DTO）、pages/settings/index.tsx（桥接 Tab 供给策略选择器四档）、
  api/requests.ts + pages/logs/RequestLogPanel.tsx（preflight 状态类型/标签/筛选项）。
- 测试：tests/unit/test_precheck.py（11 个）。

### 验证
- 单测 **161 passed**（+11：引用提取（已知输入名分类 + *_name 兜底）、扫描（modal 缺节点/缺模型/
  supplyable、local 云端专有、索引为空降级）、策略（off/ok/未知取值放行、strict 无选项、ask 带选项、
  auto 三路径、供给成败清单挪移）、拦截文案、auto_supply（真实路径拼装 argv、无分类/本地缺文件/
  CLI 失败三态））。ruff 全量 68 = 基线；`tsc --noEmit` 0 错；`pnpm build:embed` 成功。
- 真机验证（dev 8189）：**ask**——GhostNode 图 → 409「云端（Modal）缺少节点类型 GhostNode…未产生任何
  执行与费用」+ install_nodes 选项；两端都缺的模型图 → 409 + missing_models 选项。**auto**——
  本地真实放置 zz 演示模型 + GhostNode → 真实 `modal volume put` 上传数据卷（rule_trace
  auto_supply.uploaded 确认，卷 ls 可见）→ 剩余缺节点按 ask 拦截（supplyable 已清空、无陈旧提示）；
  演示产物已清理（卷 rm + 本地删除），preflight 复位 off。日志页出现独立「预校验拦截」标签
  （截图 tmp/gui-test-screenshots/m3_preflight_setting.png、m3_preflight_logs.png）；设置页四档
  选择器渲染正确。

## 二十、节点清单采集补全：Manager API 兜底 + diff 持久化缓存（M2-1 余项 · 2026-10-02）

### 背景
M2-1「节点清单采集（本地 custom_nodes 扫描 + Manager API 兜底）与 object_info diff 缓存」的
两项收尾（本地扫描 + 云端 manifest + 包级差异矩阵 + 单文件哈希对比此前已落地）。

### 实现
- `nodes_mgr/manager_api.py`（新）：ComfyUI-Manager API 兜底采集。本地 ComfyUI 在线且装有
  Manager 时抓 `/customnode/getlist`，解析（多形态容错：顶层 dict 包 custom_nodes/nodes 列表、
  顶层裸列表、repo 取 reference 或 files[] 首个 URL 形态值）出 {归一化匹配键: repo/title/version}，
  按 cnr_id/归一化名称匹配增强目录扫描结果：非 git 包补 repo（打 repo_via_manager 标记）、
  git 包补注册表版本与展示名。成功缓存 5 分钟/失败 30 秒 + 单飞。纯增强设计：ComfyUI 离线、
  未装 Manager、格式不识别一律静默降级（online=false + error），绝不影响主清单。
- `nodes_mgr/diff_cache.py`（新）：diff 持久化缓存 `~/.comfybridge/nodes_diff_cache.json`
  两个独立区块——矩阵区块短 TTL 120s（失败结果不缓存、app_state 属运行时状态不进缓存、
  命中时 app_state 每次现取）；deep class_type 区块无 TTL 长期持久（重启不丢），只有
  force=1 或缺失才重拉云端 object_info（避免每次进页面重复触发容器冷启动计费）。
  同步/移除节点后主动失效矩阵区块（deep 块与卷内容无关，保留）。
- `api/v1/nodes.py`：/nodes/diff 拆出 `_compute_matrix()` + 缓存层，新增 `force` 参数；
  /nodes/local 接入 Manager 增强（响应新增 manager 块）；sync/remove 失效矩阵缓存。
- 前端：差异矩阵 Alert 展示「矩阵缓存于 HH:mm:ss / class 对比缓存于 HH:mm:ss（避免重复冷启动）」；
  「重新扫描」改为强制现算（refreshNodeDiff force=1，结果回写缓存后失效查询回读）；
  本地清单表格新增版本列与 Manager 标签（repo_via_manager）。

### 验证
- 单测 18 个（test_node_cache.py）：缓存 roundtrip/TTL 过期/失败不缓存/失效保留 deep 块、
  Manager 解析多形态/增强匹配/fetch 降级与缓存、端点级缓存命中/force/失败不缓存/deep 持久
  （重拉只 1 次）/sync/remove 失效/local 增强与离线静默。全套 179 passed。
- ruff 68 基线持平；tsc/build:embed 干净。
- 真机：现算 19.2s → 缓存命中 2.1s（app_state 实时）→ force 重算正常；deep 现算 76.9s
  （云端 966 class）→ **后端重启后 deep 调用 1.5ms 从盘上缓存返回**、矩阵缓存命中 2s；
  /nodes/local 在 ComfyUI 离线时 manager.online=false + error，主清单完好。
  截图 tmp/gui-test-screenshots/m2_diff_cache.png。

### 关键发现
- **直接调用 FastAPI 端点函数时 `Query(False)` 默认值是真值对象**（非 False）——单测直调
  /nodes/diff 会让 force/deep 恒为真：缓存永不命中 + 意外触发真实云端 object_info 拉取
  （40s Modal CLI 超时）。端点级测试必须显式传 `deep=False, force=False`。生产路由不受影响。
- 本机 ComfyUI 未安装 ComfyUI-Manager，Manager 兜底的在线增强路径无法真机实测——解析契约
  以单测固定的多形态样例为准（防御式解析 + 任何失败静默降级，在线机器上装 Manager 后即生效）。
- deep class_type 持久缓存是「防重复冷启动计费」的关键：旧版每次 deep 都重拉云端 object_info
  （本次实测容器已缩容，force 重拉花了约 3 分钟）；缓存后进页面零成本，显式 force 才重付一次。

## 二十一、同步任务 JobDrawer（M2-6 余项 · 2026-10-03）

### 背景
前端界面规划 §5.2「长任务提交后立即返回 job_id → 自动打开 JobDrawer → 用户可离开页面，顶栏出现全局任务角标」。
此前工作流同步（POST /workflows/sync）与节点同步（POST /nodes/sync）是阻塞式 HTTP——批量动作要挂住请求
直到全部 volume put/get 完成，页面无中间反馈；镜像重建/卷移动/URL 直下各自已有作业面不动。

### 实现
- `sync/jobs.py`（新）：内存态通用作业注册表。job = {id, kind, label, state(running|ok|error|cancelled),
  total, done, items[], logs[], error, cancellable, created_ms, finished_ms}；runner 为 async 函数，
  经 JobHandle（log/advance/check_cancel）汇报；协作式取消（条目间检查点），任何异常兜底落到作业状态；
  保留最近 100 个（运行中永不清理）。全部条目失败/无条目 → error 态（区别于部分失败）。
- `api/v1/jobs.py`（新）：GET /jobs（列表+运行中计数）、GET /jobs/{id}、POST /jobs/{id}/cancel。
- 工作流同步与节点同步任务化：预检失败（未配凭证/目录缺失/空清单）直接返回 {ok:false,error} 不开作业；
  否则立即返回 {ok:true, job}，原 apply/同步逻辑原样搬进作业体（逐条 advance + log，节点同步经
  _SYNC_LOCK 串行化避免并发作业交错写 manifest；工作流同步沿用 _APPLY_LOCK）。
- 前端：`common/JobDrawer`（焦点视图：kind/state 标签、进度条 done/total、逐条结果、最近 12 条日志、
  取消按钮；列表视图：其余作业概览）；Shell 顶栏「任务」按钮 + Badge 运行中计数（useJobs 15s 空闲/3s
  运行中轮询，与页面 useJob 共享 react-query 缓存零额外请求）；页面经 CustomEvent（JOB_DRAWER_EVENT）
  唤起抽屉。工作流页与节点页改为：提交 → 打开抽屉 → useJob 轮询完成态 → 统一回调（失效查询 + 原有
  结果弹窗/「重启容器生效」确认保留）。

### 验证
- 单测 9 个（test_sync_jobs.py）：生命周期/异常兜底/全失败/空条目/协作取消/不可取消守卫/容量修剪
  （运行中保留）/API 面（404/已结束不可取消）；test_workflows_sync 的 apply 测试迁移到任务化契约
  （提交+wait_job，断言 items）；test_node_cache 的 sync 失效测试同步迁移。全套 188 passed。
- ruff 68 基线持平；tsc/build:embed 干净。
- 真机：临时工作流 cb-job-test.json → POST /workflows/sync 即返 job（running 1/1）→ 真实 volume put
  完成（state ok、log「push cb-job-test.json → 完成」）、同步矩阵变 synced；websocket_image_save.py
  重推经 /nodes/sync 同样 ok（item ok + manifest 合并后收尾）；临时文件与卷副本已清理。
- UI：顶栏「任务」按钮 + Badge；抽屉列表与焦点视图（进度条/条目/日志/其他任务）截图
  tmp/gui-test-screenshots/m2_job_drawer.png；openJobDrawer 事件自动弹出 + 焦点视图 DOM 断言通过。

### 关键发现
- **验证期间 Modal CLI 网络持续抖动**（同步矩阵 GET 长期挂起、health 探测转未接入，同 §十八 43s 模式），
  「页面点击发起同步」的完整点击链路无法在抖动窗口内演示——自动弹出改经与页面完全相同的
  openJobDrawer CustomEvent 路径验证（真 job id + 焦点视图 DOM 断言）；页面代码路径仅剩
  startWorkflowSync resolve 后调用 openJobDrawer 一行直连。刷新为轮询（SSE 留作规划增强）。
- useJob 直接返回 job 本体（解包 {job}），页面 effect 以「离开 running 态且未处理过」为完成信号，
  快任务首拍即终态也能正确回调（jobHandled ref 去重）。

## 二十二、兼容性矩阵测试（M4 余项 · 2026-10-03）

### 背景
规划 §10 测试策略：兼容性矩阵 = 远端 ComfyUI 锁定 2 个版本组合跑核心链路，防止上游协议漂移破坏桥接
（§9 对应风险：「ComfyUI API/前端协议快速演进」高等级）。落地为 `backend/tests/compat/` 离线夹具矩阵。

### 改动
- `tests/compat/fixtures/comfyui/{v0.37.2,v0.38.0,v0.38.2}/`（新）：10 个协议夹具/版本——POST /prompt
  应答与 400 拒绝、WS 事件三态序列（success 含 status/execution_start/executing/progress/
  execution_cached/executed/execution_success/executing{node:null} + 他任务帧）、history 三态条目
  （success 覆盖 images/gifs/video/audio 四桶 + 子目录 + meta 平铺）、/internal/logs/raw、
  object_info 顶面。v0.37.2 = M0-3 参照版、v0.38.0 = 当前云端 pin、v0.38.2 = 本地权威版。
- `tests/compat/manifest.json`（新）：每版本核心协议源码 sha256 指纹 + 每夹具的提取点
  （server.py post_prompt / execution.py task_done / main.py hijack_progress 与 task_done 调用点 /
  internal_routes.py /logs/raw / app/logger.py entry{t,m}）；升版 SOP：复制夹具目录 → 更新指纹 →
  跑矩阵，失败项即漂移点。
- `tests/compat/test_compat_matrix.py`（新，40 用例 = 13 面 × 3 版本参数化）：runner/client.post_prompt
  （ack 透传 + 400 上浮）、executor._ws_pump（转发集 + prompt_id 映射 + 终态捕捉 + 他任务过滤 +
  二进制/非 JSON 容错）、get_history 解包（空/404/断连 → None）、_classify_entry 三态 + 空 status 兜底、
  _download_outputs 四桶/子目录/越界拒绝、fetch_container_logs 归一（\r 与 [ERROR] 行 + 失败返回空）、
  fetch_object_info 顶层键集（diff 矩阵 class_types 唯一消费面）、ws_relay._tap_event 本地状态映射、
  _track 双通道（WS 快路径终态先行 / history 权威带回条目）。

### 验证
- 矩阵 40 passed（0.8s，离线无网络）；全套 228 passed（188 + 40）。
- 负向验证（有牙）：改坏夹具键名 exception_message → 仅对应版本分类用例红（1 failed）；还原 → 40 绿。
- ruff：全量 69 = stash 实测基线 69（新增 0；此前 FIXLIST 记录的 68 与本次 concise 口径差 1，以
  stash 对照实测为准）；改动文件初稿 2 处（RET501/PLR1711）当场消除。
- 本切片纯测试基建，无运行时代码改动，无需重启服务/真机回归。

### 关键发现
- **三版本核心协议文件逐字节相同**：execution.py 三版 sha256 一致，server.py 仅差资产导入守卫
  （v0.37.2 vs v0.38.x）→ 当代（0.37–0.38）协议面稳定，矩阵当前价值是回归钉子 + 升版 SOP，
  未来 pin 追随本地升版时自动捕获漂移。
- **上游 completed 语义**（main.py task_done 调用点）：`completed=e.success` —— 错误/中断 history
  条目 completed=false、status_str="error"；夹具初稿误写 completed=true 会被 _classify_entry 判成
  success，据源码修正。桥接器判据（completed or status_str=="success"）与上游真相吻合。
- **云端 executed 帧被泵丢弃是设计使然**：_ws_pump 转发集不含 executed；桥接器在输出落盘后从
  history 权威重建 executed 事件（保证浏览器收到即请求 /view 时文件已就位）。
- WS 快路径 kind 走桥接器状态词汇表（execution_interrupted → cancelled，与日志页 queued/running/
  done/error/cancelled 对齐）；history 路径 _classify_entry 返回 "interrupted"，_finish 再归一。
- 「进 CI」落点：仓库无 remote，矩阵以 pytest 全量承载（18.7s），接 CI 后直接纳管 tests/compat。

## 二十三、版本对齐历史留痕（M2-5 余项 · 2026-10-03）

### 背景
规划 §3.3 版本对齐行（M2-5 第一片 2026-09-30）标注的余项之一（另一项「与节点固化的重建入口合并」
已于 M2 收尾完成）：对齐操作此前只有瞬时 job 状态，重启即丢，无「何时/从哪版/到哪版/结局如何」的
留痕。本片补齐。

### 改动
- `nodes_mgr/deploy.py`：新增一次性收尾回调 `set_finish_sink/_fire_sink`——run_deploy 四个收尾点
  （CLI 缺失/非零退出→rolled_back、超时→timeout、成功→ok）各 fire 一次即清除，回调异常吞掉不影响
  部署任务状态；`begin_exclusive` 清除上一次未触发的注册（防串）。共享部署任务保持单飞语义不变。
- `nodes_mgr/version_align.py`：历史写入器——JSONL 追加到 `~/.comfybridge/version_align_history.jsonl`
  （记录 ts_ms/time/trigger/from_pin/to_tag/outcome/error/duration_ms/local_commit；留痕失败静默）；
  `read_history(limit)` 新的在前、坏行跳过；`sink_for()` 把 deploy 收尾口径直译为历史 outcome；
  `start_align` 挂 sink（含本地 commit），pin 已一致的 noop 也留一条（不经 deploy，直接记）。
- `nodes_mgr/image_rebuild.py`：align_version=True 且 pin 实际变更时同样挂 sink（trigger=rebuild）；
  pin 无变更只固化时不产生对齐历史。
- API：GET /nodes/version 响应新增 `history`（最近 20 条）。
- 前端：节点页 VersionBanner 下新增「对齐历史（N）」折叠面板（Collapse+List）：时间 · from→to ·
  结局 Tag（成功/已回滚/超时·不确定/无需变更）· 随镜像重建标记 · 耗时 · 错误详情 Tooltip；无历史不渲染。

### 验证
- 单测 +9（全 237 passed）：align ok/回滚/超时/noop 四结局留痕 + sink 一次性清除、坏行跳过与新在先、
  留痕失败静默；rebuild 带 pin_change 注册 sink 并写 rebuild 记录、不带变更/不带对齐不产生历史
  （reset_deploy_job fixture 顺带清 sink 防泄漏）。
- ruff 新增 0（全量 69 = stash 基线 69；deploy 新增 S110 改 contextlib.suppress 消除）；
  tsc 0 错；pnpm build:embed 成功。
- 真机（零成本路径）：重启 ：8189 → GET /nodes/version 返回 history:[] → 临时把 pin 文件设为本地版本
  v0.38.2 → POST /nodes/version/align 真实端点返回 ok（「无需重建」）→ 真实历史记录落盘
  ~/.comfybridge/version_align_history.jsonl（noop，时间戳/trigger 齐全）→ pin 还原 v0.38.0（git 无 diff）。
- UI（浏览器 DOM 断言 + 截图）：节点页版本告警下方出现「对齐历史（1）」，展开后记录行完整渲染
  （10-03 19:00 · v0.38.2 → v0.38.2 · 无需变更 Tag），截图存档 tmp/gui-test-screenshots/m2_align_history.png。

### 关键发现
- deploy 的 ok/rolled_back/timeout 三结局均真实发生过（此前切片），本片用一次性 sink 把结局接给
  历史而非轮询 _JOB——收尾即写，无竞态窗口；noop 路径不经 deploy，单独直记。
- 「pin 已一致」的对齐尝试也留痕（outcome=noop）：审计视角「用户试过对齐」与「什么都没发生」
  都值得记；noop 不记 duration/error。
- 真实 full deploy 的 ok/rolled_back/timeout 记录未现场重演（避免一次真实镜像重建计费与 Modal 网络
  抖动窗口），三结局由 _FakeProc 单测路径覆盖，落盘代码与 noop 为同一条 append_history 通路。

## 二十四、云端执行 0 文件误报完成（真机缺陷 · 2026-10-03）

「重启项目，进行完善的测试」拉起双端做真实生成时发现：云端通道每次执行都
`输出回传完成：0 个文件`，记录却标 done——浏览器收到 execution_success 但无
executed/无图，媒体库不落盘。自 M1 起每次真实云端生成均命中（#38-#49 共 7 连
失败），此前未被发现是因为测试都止步于「记录 done」。

- **现象**：云端 history 条目事后查验 outputs 完整（/view 可取 1.6MB 图），桥接器
  当场却拿不到。旁路观察（每 2s 直拍 /history + 读容器日志）钉死时序：执行期间
  /history 挂起（读超时都不返回）；条目在 task_done 一次性带完整 outputs 出现，
  从无中间空态（get_history 与 task_done 同持 mutex，无撕裂读）。
- **根因（两个叠加）**：① 云端 WS 泵存活期间，新的 /history HTTP 请求持续挂死
  （Modal web_server 边缘或 aiohttp 层串扰，机制待上游查证；无 WS 的裸请求一切
  正常——旁路脚本全中、#43 泵恰好未连上则轮询全 200、#41 泵未连上则 160×200）。
  ② 泵原本在 _track 的 finally 才关闭，宽限轮询全程被泵毒化；旧读超时 30s 又使
  一次挂死轮询耗尽整拍节奏。
- **修复**：终态信号一到立即 `ws_task.cancel()` 再进入宽限；success 宽限轮询窗
  `_SUCCESS_GRACE_S=120s`（扛挂死恢复 + 无输出工作流合法收尾），拿到条目走与
  权威路径同款 `_classify_entry`；`get_history` 读超时 30s→8s 快速失败。到期仍无
  条目 = 无输出节点的工作流，合法按 done 收尾。
- **验证**：兼容矩阵新增 6 项回归（WS success 竞态必须等到条目 / 无条目合法
  收尾 × 3 版本），243 全绿；真机两连跑（真实执行 + WS 泵全程存活，即此前必现
  场景）均 executed 事件到浏览器 + 1 文件落盘 + 媒体库可见（00016/00017）。
- **诚实记录**：调试期间真实云端执行 12 次、约 1141 GPU 秒（≈$0.62），其中 #41
  因桥接重启跟踪丢失（cancelled，按设计）、#45 为冷启动窗口的云端瞬时 400
  拒绝（重试即成功），其余均 done。 execution.py 三版本同哈希、main.py 两版本
  task_done 调用点一致，均已逐一排除后定位到泵串扰。

## 二十五、e2e 自动化 + CI（M4-4 e2e 余项 · 2026-10-03）

仓库有 remote 后补齐规划最后一项：Playwright 全栈 e2e + GitHub Actions。

- **架构**：真实后端 + 真实嵌入构建产物，零 mock。webServer 由 playwright 配置自动
  拉起（默认 ../backend/.venv 的 Python，CI 里 uv sync 生成同一路径；E2E_PYTHON 可
  覆盖），就绪探测 /api/v1/health，端口 8199 避开开发实例；浏览器用系统 Edge
  （本地与 Windows runner 都预装，免 Chromium 下载，E2E_BROWSER 可覆盖）。
- **配置隔离（新增 CB_CONFIG_DIR）**：config.toml/进程状态/对齐历史/节点缓存等 7 处
  Path.home()/.comfybridge 收口到 core.config.config_dir()，环境变量整体重定向。
  e2e 后端跑在 frontend/e2e/.tmp-config 空目录上，离线空态完全确定（对齐历史
  折叠面板不渲染、无任务、无请求）。未设置时行为不变，243 项单测全绿。
- **用例（frontend/e2e/flows.spec.ts，5 条）**：首次配置向导契约（空配置 600ms 自动
  弹、X 关闭、localStorage 跳过本会话不再弹）、仪表盘核心元素、全页面遍历（9 页
  有内容且无错误边界，等 main 文本稳定再断言）、节点管理空态与双 Tab、设置六
  Tab + 设置同步 + JobDrawer 空态。
- **踩坑**：① 空配置会自动弹配置向导，ant-modal-wrap 拦截一切点击——先关弹窗再
  断言（Esc 关不掉，向导 keyboard 禁用，用 .ant-modal-close）；② getByText 重名
  文案要 scope 到 banner；③ 节点页数据异步加载，过早采样只有 9 字符 Tab 文案，
  等「长度>20 且连续两次采样一致」才算稳定。
- **CI（.github/workflows/ci.yml，windows-latest 双 job）**：backend-tests（uv sync →
  ruff 基线门禁「允许存量 68、拦截新增」→ pytest）+ e2e（build:embed → uv sync →
  playwright test，失败上传 test-results 现场）。推送 main / PR 触发，可手动
  dispatch。
- **诚实记录**：e2e 在本机跑绿（5/5，两轮），CI 尚未实际触发验证（推送后首次
  run 才见真章）；后端 data/ 目录为仓库相对路径，e2e 与真实请求库共享（只读流
  断言不依赖记录数），CI 上该目录为空即天然空态。
- **CI 首轮抓到的测试卫生问题（已修）**：① ruff 门禁步骤被 pwsh 透传 ruff 的
  exit 1 而误判失败——脚本尾部显式 exit，门禁只拦新增；② 6 个 WS 泵/跟踪用例
  未隔离 get_settings，本机靠真实 config.toml 侥幸通过（无配置环境下 ws_url 会
  静默炸掉泵、零帧）——补 rc_mod.get_settings 打桩；③ runner_auth 的
  _patch_settings 漏了 runner.get_settings 与 modal_configured 两个消费方——补齐。
  修复后以「假 USERPROFILE」（等价 CI 无配置环境）+ 真实环境双跑 243 全绿。

## 二十六、P3-1 ruff 存量清零（2026-10-11）

路线图第三轮最后一项：把 CI 里「允许存量 68、只拦新增」的临时门禁升级为零违规门禁。

- **分批偿还**：先 `ruff check . --fix` 跑掉 27 条安全 autofix（unused-import / unused-noqa /
  UTC 别名 / 引号注解 / import 排序 / SIM114 同类分支合并），64 → 40；再按类别人工处理。
- **DTZ005（12 条）**：本地墙钟时间改 `datetime.now().astimezone()`，保留本地语义并附
  tzinfo；`now_iso()` 与 runner 控制台时间戳的落库/展示格式不变，test_cost.py 同步改。
- **PLW1510（8 条）**：netstat / tasklist / taskkill / powershell 查询与 modal volume put、
  modal container 调用补 `check=False`，明确这些靠 returncode/stdout 判断、不抛异常。
- **S110 / S112 / BLE001**：静默兜底改 `contextlib.suppress(...)`（语义不变、更显式）；
  ws_relay / runner.client / executor 里带 logger.*(exc_info=True) 的兜底去掉已失效的 noqa；
  local_stream / logs.py / nodes 的边界捕获保留 except Exception 并补原因。
- **其余**：SIM102（嵌套 if 合并）、SIM103（直接返回布尔）、C408（dict() 改字面量）各就地
  修正；tests/regress_local_ws.py 的顺序阻塞 HTTP 标注 noqa: ASYNC210（手动脚本，非 pytest 模块）。
- **收口**：.github/workflows/ci.yml 的 ruff 步骤从「基线 68」改为「任何违规即失败」；
  `ruff check` 全仓 0 违规，pytest -q 307 passed / 9 skipped。

## 二十七、P2-1 管理面鉴权边界补全（2026-10-11）

路线图第四轮第一项：P0-5 已给管理面加 Bearer/Basic 与监听保护，本项补齐 Origin 校验、
审计日志与反向代理部署说明。

- **Origin 校验（CSRF）**：`management_auth` 增加同源比对；跨站 `Origin` 的管理请求返回 403，
  可信前端/反代域名经 `CB_ALLOWED_ORIGINS`（逗号分隔）白名单放行；无 Origin 的脚本请求仍受令牌约束。
  反代场景读 `X-Forwarded-Proto` 参与同源判定。
- **审计日志**：新增 `app/core/audit.py`，JSONL 落 `<配置目录>/audit.log`（`CB_AUDIT_LOG` /
  `CB_AUDIT_LOG_PATH` 可覆盖），超 5MB 轮转 `.1`。写操作 + 所有 401/403 拒绝必记，成功的只读
  GET/HEAD/OPTIONS 不记；只记方法/路径/状态码/客户端 IP（含 `X-Forwarded-For`）/Origin/认证方式/原因，
  **不含 Authorization、token 与请求体**。写入失败只记 logger，绝不阻断请求。
- **部署文档**：新增《反向代理与多用户部署》，覆盖管理面 vs 数据面边界、令牌配置与生成、
  Origin 白名单、审计说明、Nginx 反代示例（Host/X-Forwarded-Proto/X-Forwarded-For 透传、SSE/WS 超时）
  与部署前检查清单；README「管理面访问边界」与 `config.example.toml` 同步。
- **验证**：`test_management_auth.py` 14 passed（含同源放行/跨站 403/白名单/无 Origin/无令牌跳过、
  审计记录与脱敏、只读不记、默认随令牌开关）；后端全量 315 passed / 9 skipped；ruff 全仓 0 违规。

## 二十八、P2-2 单实例边界与作业状态存储接缝（2026-10-11）

路线图第四轮第二项：明确单实例边界，并把作业状态持久化抽象出来，避免未来迁移
Redis / PostgreSQL 时重写每个业务模块。

- **默认内存实现**：新增 `app/core/job_store.py`，定义 `JobStore` 协议与线程安全的
  `InMemoryJobStore`；提供 create/get/list/save/delete/prune/clear，修剪语义区分
  「总数上限」（sync，运行中占容量）与「已完成数上限」（卷同步，运行中不占容量），
  运行中记录永不清理；`set_default_store` 供测试替换。
- **首批接入**：`app/sync/jobs.py`（同步任务注册表）与 `app/modal_volume/jobs.py`
  （卷同步作业表）改经存储接缝读写；每次日志/进度/取消/终态变更都调用 `save`，
  API 返回结构、事件名与前端契约不变；默认仍是进程内、重启清空。
- **发布文档**：README 新增「单实例限制（P2-2）」；《反向代理与多用户部署》新增
  「单实例边界」小节，明确同一配置目录 + 同一后端实例 + 单进程，列出多实例迁移
  需要改的共享存储、事件流与文件类状态，并在检查清单补「不要对 `/api/v1` 负载均衡」。
- **验证**：`test_job_store.py` 新增 8 项（存储语义 / 两种修剪口径 / 默认存储替换 /
  按值保存模拟外部实现 / sync 接入），与 `test_sync_jobs.py` 合计 17 passed；
  定向 ruff 0 违规。

## 二十九、P2-4 预算护栏边界与账单告警联动（2026-10-11）

路线图第四轮第三项：把「本地估算护栏」的边界写清楚，并让成本对账在单价失真时
主动提示。

- **后端偏差判定**：`app/cost/reconciliation.py` 新增 `ratio_warn_factor=1.5` 与
  `diverged` / `divergence_message`：Modal GPU 计量 ÷ 本地估算落在 `[1/1.5, 1.5]`
  之外时告警，提示核对 `gpu_hourly_rate_usd` 并说明本地护栏不代表 Modal 账户上限。
- **界面与告警文案**：成本报表对账卡片新增偏差 Alert；设置页「护栏与预算」、
  仪表盘超限横幅与顶栏预算角标统一标注「本地护栏，不是 Modal 账户级硬上限」，
  并建议同时在 Modal 平台设置预算/消费告警。
- **文档**：README 新增「预算护栏边界（P2-4）」，写明估算口径、只覆盖经桥接器的
  请求、与 Modal 平台预算配合使用的建议。
- **验证**：新增偏差告警与边界值（恰好 1.5 不告警）两项单测；成本模块与后端全量
  回归保持通过。

## 三十、P2-5 云端提交前隐私确认（2026-10-11）

路线图第四轮最后一项：首次把工作流分流到 Modal 时给出一次数据边界说明，并提供
可撤销的「不再提示」开关。

- **后端无状态挑战**：新增 `app/proxy/privacy.py`，`/prompt` 在最终决策为 `modal`
  且请求未带 `X-Bridge-Privacy-Confirmed: 1` 时返回 `409
  privacy_confirmation_required`，响应体列出四类出境数据（工作流图快照、输入文件、
  必要元数据、云端执行日志）；判定放在落库与改写 prompt_id 之前，不产生执行记录。
- **页面一次性弹窗**：数据面透传层对 ComfyUI 根 HTML 注入确认脚本，拦截 fetch，
  遇到挑战时原生弹窗确认后带请求头重试，并按浏览器 localStorage 记忆「不再提示」；
  不改本地 ComfyUI 源码，其余响应仍流式透传。
- **可撤销开关**：`bridge.privacy_confirm`（默认开启）新增到配置模型、示例配置与
  设置页「桥接」Tab 的 Switch，可随时关闭或重新开启。
- **验证**：`test_privacy_confirm.py` 新增 15 项（开关/确认头取值/挑战字段/注入脚本文本/
  `/prompt` 409 与放行/开关关闭），定向 ruff 0 违规。

## 三十一、P3-2 文档与代码同步（2026-10-11）

路线图 P3-2：把滞后的发布文案收敛到与代码一致，并加一道文档一致性门禁。

- **README 收敛**：里程碑大段流水账改写为「当前状态（2026-10-11）+ 核心链路 +
  最近变更」；历史实现细节与真机验证记录指向 `FIXLIST.md`，避免 README 随开发
  反复改版失真。
- **路线图同步**：P3-2 标注完成并补结果块，第四轮迭代顺序补入 P3-2，发布验收
  清单新增「文档状态与实际代码一致」一项。
- **口径统一**：路线图/FIXLIST 只记录完成度与偏差，架构与验收标准留在计划书
  §6–§7，减少多处并存造成的过期描述。
- **验证**：`git diff --check` 干净；本轮仅 Markdown 变更，不涉及运行时代码。

## 三十二、P3-1 收尾：mypy 门禁接入（2026-10-11）

P3-1 遗留的「mypy 尚未形成发布门禁」项闭环。

- **配置固化**：`backend/pyproject.toml` 新增 `[tool.mypy]`（`ignore_missing_imports`
  + `files = ["app"]`），CI 在 Pytest 后新增 `uv run mypy`，任何错误即失败。
- **存量清零**：40 条错误逐类处理——Protocol 内 `list` 同名方法遮蔽改用模块级类型
  别名；`Any` 容器补显式注解（usage / jobs / uploader / nodes / volume / launch /
  manager_api / logs）；`AppConfig(**merged)` 与 `AppConfig(local=..., bridge=..., modal=...)`
  改走 `model_validate`；`precheck` / `routing` 的联合类型收窄；`resolve.precheck`
  的 pip 冲突结果改用局部变量避免与静态冲突表混淆。
- **真实缺陷修复**：`cost/modal_billing.py` 的 `parse_report` 在 CLI 返回非列表时
  执行 `{...}[:300]`，会抛 `TypeError` 而不是返回 `ok=false`；改为直接返回错误体。
- **验证**：`mypy`（`app` 81 文件）0 错误；`ruff check .` 0 违规；后端全量
  `340 passed, 9 skipped`；定向 `test_cost` / `test_job_store` / `test_sync_jobs`
  合计 43 passed。

## 三十三、Playwright 失败现场上传实测（2026-10-11）

发布验收清单最后一项可在无 Modal 环境下闭环的条目：确认 CI 的
`上传失败现场` 真的能把可定位的产物交出来。

- **做法**：临时分支 `test/e2e-artifact-verify` 加一个故意失败的探针用例，
  `workflow_dispatch` 触发 CI（[run 38104366159](https://github.com/zikdjh/comfy-bridge/actions/runs/38104366159)）。
  `e2e` job 按预期失败，`if: failure()` 的 `上传失败现场` 步骤执行成功；
  `backend-tests` 不受影响仍全绿。
- **下载与定位**：`gh run download 38104366159 -n playwright-artifacts` 取回现场。
  因为 CI 上 `retries: 1`，共两份：`...-intentional-failure/` 与 `...-retry1/`。
  每份含 `error-context.md`（失败用例名、文件位置、期望/实际值、页面快照、
  控制台与网络摘要）、`test-failed-1.png` 与 `trace.zip`（内含 DOM 快照、网络
  与 screencast，可 `playwright show-trace` 打开）。
- **本地一致性**：同一探针在本机跑也产出同样三类文件，确认失败现场行为在本地
  与 CI 一致。
- **清理**：探针文件、`tmp/artifact-verify`、`frontend/test-results` 与远端临时
  分支均已删除；`develop` 未受影响。

## 三十四、版本对齐/节点同步真机验收与四处真实缺陷修复（2026-10-11）

在真实 Modal 上把「本地 vs 云端版本不同 → 对齐版本 + 同步节点」跑成实机验收，
沿途修掉四处只有真机才暴露的缺陷。分支 `feature/live-verify-version-node-sync`。

> 缺陷编号、严重度、证据索引与待修复项（LV-06）统一登记在
> `docs/真机验收缺陷清单.md`；本文只记修复过程与验证结果。

### 1. CLI 输出编码：`modal deploy` 成功却被判失败

- **症状**：中文 Windows 上点「对齐云端并重建」，UI 报「部署失败，已回滚 pin」，
  但镜像其实已构建完成；CLI 退出码为 1。
- **根因**：CLI stdout 默认 GBK，成功路径打印 `✓ / 🔨 / 🎉` 触发 `UnicodeEncodeError`。
- **修复**：`app/modal_volume/client.py` 新增 `cli_env()`（`PYTHONUTF8=1` /
  `PYTHONIOENCODING=utf-8` / `PYTHONWARNINGS=ignore` + 凭证），
  `nodes_mgr/deploy.py`、`cost/modal_billing.py` 统一改用；与调用方 `decode("utf-8")` 对称。
- **验证**：同一台机器加该环境后 `modal deploy` 8 秒成功；
  `test_modal_volume_client.py` 新增 3 项、`test_version_align.py` 新增 1 项。

### 2. 版本对齐真机实测（本地 v0.39.2 ↔ 云端 pin v0.38.0）

- 本地 ComfyUI 实为 **v0.39.2**（`/system_stats`），云端 `lock/comfyui_version.txt`
  仍 pin 在 **v0.38.0**，`/nodes/version` 正确报出 drift。
- UI 真实点击「对齐云端 → v0.39.2」→「开始重建」：`state=ok`、20 秒完成、
  pin `v0.38.0 → v0.39.2`、history `outcome=ok duration_ms=16782`；
  复核 `/nodes/version` 为 `drift=false`、`pinned=v0.39.2`。
- 修复前那次失败留下的 `outcome=rolled_back`（100s）历史与修复后的 `ok` 形成对照。

### 3. 节点同步真机实测

- 用无依赖探针节点（`custom_nodes/comfybridge-sync-probe/__init__.py`）走 UI
  「安装到云端」：对话框回「已同步 1 个节点到云端数据卷」，卷上出现
  `custom_nodes/comfybridge-sync-probe`（4.0 KiB），矩阵 `missing_cloud → same`。
- 「从云端移除」后矩阵回到 `missing_cloud`；本地探针与云端副本均已删除，
  矩阵回到 `synced 2 / only_local 0 / only_cloud 0`。

### 4. 图生图：热容器读不到 `modal volume put` 写入的输入

- **症状**：云端图生图报 `node_errors.1: image - Invalid image file: <name>`，
  而 `modal volume ls ... input` 里文件确实存在。
- **根因**：Modal Volume 是**快照语义**，外部 `volume put` 对**已在运行**的容器不可见。
  冷启动容器能读到（探针 `/view?type=input` 返回 200 / 394894 字节），热容器读不到。
- **修复**：`runner/client.py` 新增 `post_input_file()`，走容器内 ComfyUI
  `/upload/image`（该端点只落原始字节，不做图片解码校验）；`executor._upload_inputs`
  优先走容器内通道，端点不可用时回落 `volume put`。
- **A/B 实测**（同一个热容器 `ta-01M4MFEG…`、同一个从未上传过的输入名）：
  旧代码 `FAILED … Invalid image file`；新代码 `1 passed`，日志
  `输入已推送云端(comfy): bridge_live_input_v3.png`。另补 7 条单测。

### 5. 本地请求终态：没有浏览器就永远停在 queued

- **症状**：纯 HTTP 提交（脚本/集成测试）本地文生图，请求记录一直 `queued`，
  即使图已写入 `output/`；状态只由浏览器 WS 中继回写。
- **修复**：新增 `app/proxy/local_tracker.py`，受理成功后后台轮询 ComfyUI
  `/history/<prompt_id>`（该端点只在任务结束才出现条目），把
  `queued → running → done/error/cancelled` 收敛到记录；`main.py` 关停时释放客户端。
  新增 live 用例 `test_local_text_to_image`（`CB_LIVE_LOCAL_FIXTURE_JSON`）与 8 条单测。
- **实测**：本地真实出图（RTX 4060，v0.39.2）`done / final_backend=local`，
  生成 `bridge_live_local_00002_.png`（388715 字节）。

### 6. WS 泵阻塞 HTTP：中断请求从未送达，请求挂到 3600s

- **症状**：中断用例请求一直 `running` 十分钟以上；容器日志显示该 prompt
  在云端 **0.02 秒**就结束，但桥接器侧看不到终态。
- **根因**：Modal `web_server` 在 WS 泵存活期间只处理该连接，其它 HTTP
  （`/history`、`/queue`、`/interrupt`）排队直到其结束。云端提前中断且终态事件
  未被广播（泵建立晚于广播）时，泵一直挂着 → `/history` 永远读超时 →
  请求被挂到 `task_timeout=3600s`；`/interrupt` 同样排队读超时，
  **中断从未真正送达**（日志只剩「云端 /interrupt 失败:」）。
- **对照实验**：桥接器 WS 存活时 `GET /history/<pid>` → `ReadTimeout`；
  停掉桥接器后同一条 `GET /history/<pid>` → **200 + 该中断条目**。
- **修复**：`executor._track` 每 45 秒主动收泵一拍做权威 `/history` 判定，未终态再重挂；
  `interrupt_all` 改为「先收泵 → 发 `/interrupt` → 跟踪循环稍后重挂」。
- **验证**：中断用例改为换种子避免缓存命中并断言 `cancelled` → `1 passed`（11.27s，
  记录 `status=cancelled duration_ms=8208`，日志 `POST /interrupt … 200 OK`）；
  新增 3 条单测。

### 本轮门禁与 live 记录

- `ruff check app tests` 0 违规；`mypy app`（82 文件）0 错误；
  后端全量 **362 passed, 10 deselected**。
- 非付费预检 + 本地真实出图：`3 passed, 1 skipped`。
- 云端付费：文生图 / 图生图 / 中断 / 断线兜底 / 无输出 **全部通过**
  （`4 passed` 一波 + 图生图与中断各单独复验；无输出用例回传 0 文件仍合法 `done`）。

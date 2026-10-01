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

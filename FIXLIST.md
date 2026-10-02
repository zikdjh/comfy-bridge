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

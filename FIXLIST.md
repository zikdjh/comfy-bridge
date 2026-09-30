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

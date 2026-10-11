# 方案A探针：原生 Job Queue（/api/jobs）集成可行性（只读结论）

> 探针时间：2026-10-11 · 分支 `feature/round2-r2-08-jobqueue-probe` · 只读分析，未改桥接器行为
> 对象：本地 ComfyUI v0.39.2 `comfy_execution/jobs.py` + `server.py /api/jobs`
> 目的：回答 R2-08——是否值得把云端任务塞进 ComfyUI 原生 Job Queue 面板

## 1. 原生机制实测（v0.39.2）

- **数据源**：`GET /api/jobs` 由 `PromptQueue.get_current_queue_volatile()` +
  `get_history()` 生成，`get_all_jobs()` 归一化成统一 job dict（含
  `execution_start_time / end_time / preview_output / workflow_id`）。
- **真机验证**：本地 8188 直连 `/api/jobs?limit=3` 返回历史任务完整 JSON；
  经桥接器 8189 透传同样正常（catch-all 命中，无需额外接线）。
- **前端驱动**：新版 ComfyUI 前端（Job Queue 面板）读取该端点；`prompt_id`
  必须是规范小写 UUID（`validate_job_id`）——桥接器改写 prompt_id 时已遵守。

## 2. 关键发现：原生 jobs 条目没有 display name

`normalize_queue_item` / `normalize_history_item` 的字段只有
`id / status / priority / create_time / workflow_id / outputs...`——
**没有 workflow name 字段**。原生面板显示的名字来自 `workflow_id`
（用户在画布保存过的工作流 ID，前端再查本地库显示）。

这意味着：**即使把云端任务注入 `/api/jobs`，原生面板也只能显示一串
workflow_id 或空白**，除非给 `extra_data.extra_pnginfo.workflow` 塞入
完整 workflow JSON（体积大）或伪造 workflow_id（前端查不到会显示更糟）。
方案B悬浮面板读桥接器请求记录的 `workflow_name`，显示效果反而更好。

## 3. 注入路径评估（三条路都不理想）

| 路径 | 做法 | 结论 |
| --- | --- | --- |
| ① 改写 `/api/jobs` 响应 | 桥接器接管该端点，本地 jobs + 云端记录合成 | **可行且最干净**。但：云端条目缺原生必需字段（preview/outputs_count 等，可填 0），且 `workflow_id` 显示问题如 §2；前端面板会对 synthetic 条目「取消」操作发 `POST /api/jobs/{id}/cancel`——本地队列里没有该 id，返回 no-op，取消按钮无效（体验缺陷） |
| ② 向 PromptQueue 注入假任务 | 启动时 hook `PromptQueue`，把云端任务 put 进本地队列 | **不可行**。假任务会被本地执行器真的执行（找不到模型直接报错），污染执行链路 |
| ③ WS status 合成 | 已实现（`_rewrite_status_queue_remaining`） | 这是当前方案B的一部分，原生面板顶部计数已含云端任务 |

## 4. 与方案B对比

| 维度 | 方案B（现状：悬浮面板） | 方案A（接管 /api/jobs） |
| --- | --- | --- |
| 工作流名显示 | ✅ 真名 | ❌ 只有 workflow_id/空 |
| 取消云端任务 | ❌ 暂无（可加） | ⚠️ 按钮存在但无效 |
| 与原生面板一致性 | 独立悬浮，不动原生 | 深度融合，但 synthetic 条目缺字段 |
| 实现成本 | 已完成 | 后端接管 + 字段补齐 + 前端联动测试，约 2-3 天 |
| 升级兼容 | 不依赖原生面板内部 | 依赖 ComfyUI `/api/jobs` 私有 schema，**前端每次升级都可能破坏** |

## 5. 结论与建议

**不建议现在投入方案A**。决定性理由是 §2：原生 jobs 条目没有工作流名，
注入后显示效果反而不如方案B；叠加 §3① 的取消按钮失效问题，用户体验
净收益接近零，而升级兼容成本长期存在。

方案B的两条增强（低成本，可纳入后续小修）：

1. 悬浮面板任务行加「取消」按钮 → 调桥接器 `/interrupt`（按 prompt_id 转发云端）；
2. 悬浮面板按 prompt_id 排序（队列序）而非按创建时间倒序，与原生面板序一致。

R2-08 可关闭：方案B为长期形态，方案A仅保留本文档作为决策依据。

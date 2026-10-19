# ComfyBridge 全量 Web 测试暂停快照

记录时间：2026-10-19 00:15 +08:00  
基线：`main@924a49e`  
范围：Web 管理台、Web API、ComfyUI 数据面与 Modal 云端链路；不测试 exe、整合包或 Tauri。

## 结论

本轮按用户要求暂停测试，**不是全量验收完成，也未完成 368 项用例**。

当前审计记录：

| 状态 | 数量 |
|---|---:|
| 通过 | 103 |
| 失败 | 1 |
| 未执行 | 264 |
| 合计 | 368 |

恢复测试后新增 6 项通过；剩余唯一失败是 `API-018` 的旧计划文案与现接口契约不一致，功能本身已验证通过。`FLOW-004` 因 Modal Runner 当前未部署，需在新一轮云端生图后再复测。

## 已完成的验证

- `pnpm build` 通过；仅存在大 chunk 提示，无构建错误。
- 本轮相关后端单元测试 `23 passed`。
- Playwright 修复回归 `4/4 passed`。
- 配置向导与账号档案弹窗打开后聚焦弹窗内容，Escape 可关闭。
- 768、1024 宽度下设置页与日志页无页面级横向溢出。
- `bridge.url` 与 `bridge.listen` 使用实际运行时入口 `127.0.0.1:18189`，并新增单元测试覆盖。
- 本地 ComfyUI 健康探测与数据面统一改为回环直连，不再受系统 `HTTP_PROXY=127.0.0.1:9` 影响；实机 `/health` 已恢复 `0.41.2 · 5ms`。
- 数据卷用量统计增加整体超时、限流快速收敛与更低并发；最终 `computing=false`，两卷、约 40.92GB、543 个文件。
- 模型上传卡片真实点击验证：6 条会话（5 完成 + 1 MD5 失败）、清理按钮无未开始会话时禁用、删除后收敛为 5 条且提示可见。

## 服务与容器

- 已停止 Vite `15173`、主后端 `18189`、隔离后端 `18289` 与 `18389`、本地 ComfyUI `8188`。
- 已复核 `15173`、`18189`、`18289`、`18389`、`8188`、`18190-18193` 无 `LISTENING`。
- Modal 容器最后一次成功核对为 `2026-10-18 23:22 +08:00`，当时 `count=0`。
- 暂停时重新查询 Modal 容器超过 30 秒未返回，CLI 已终止。**该次超时不能视为已确认容器为 0。**
- 恢复测试前必须重新查询 Modal 容器列表。

## 待处理失败

| 用例 | 问题 |
|---|---|
| `API-018` | 仅剩旧计划文案与接口契约不一致；接口实际 `entries/workflow/model` 已通过。 |
| `FLOW-004` | 历史 `workflow_name=null` 需 Runner 部署后重新提交云端生图复测成本归组。 |

## 恢复测试顺序

1. 重新查询 Modal 容器，记录当前状态；若不为 0，确认归属后停止。
2. `modal app list --json` 当前返回 `[]`，Runner 未部署；继续云端生图前需先部署或恢复 Modal 应用。
3. 真实生图只通过 Modal 执行，并核对 `final_backend=modal`、`prompt_id`、产物和费用回查；严禁本项目在本地生图。
4. 从剩余未执行项恢复页面级真实点击和写操作回收测试，完成后立即导出记录。
5. 完成数据清理、停服和最终计数后，再更新发布结论。

## 证据

- 测试记录：`C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42\ego\comfy-bridge-web-20261018\comfybridge-web-test-record-20261018.json`
- 汇总统计：`C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42\ego\comfy-bridge-web-20261018\comfybridge-web-test-summary-20261018.json`
- 暂停状态：`C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42\ego\comfy-bridge-web-20261018\round6-stop-state.json`
- 运行时健康：`C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42\ego\comfy-bridge-web-20261018\round6-runtime-health.json`
- 本轮上传与卷统计：`C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42\ego\comfy-bridge-web-20261018\round8-upload-card-20261019.json`
- 本轮 Modal 状态：`C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42\ego\comfy-bridge-web-20261018\round8-modal-app-list-20261019.json`
- 完整测试矩阵：[`ComfyBridge-过程-全量Web测试计划-20261018.html`](./ComfyBridge-过程-全量Web测试计划-20261018.html)

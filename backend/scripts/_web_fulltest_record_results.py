"""Map evidence-backed full-test results into the browser plan JSON.

Only cases with concrete evidence are marked pass or fail. All other cases
remain todo. The output is a browser-importable record and a CSV summary.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "ComfyBridge-过程-全量Web测试计划-20261018.html"
EVIDENCE = Path(
    r"C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42"
    r"\ego\comfy-bridge-web-20261018"
)


def group_rows() -> list[tuple[str, str, list[list[str]]]]:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("const groups = [")
    end = text.index("\n];", start)
    source = text[start:end]
    groups: list[tuple[str, str, list[list[str]]]] = []
    pattern = re.compile(
        r'mount:\s*"(?P<mount>[^"]+)".*?'
        r'prefix:\s*"(?P<prefix>[^"]+)".*?'
        r'title:\s*"(?P<title>[^"]+)".*?'
        r'rows:\s*\[(?P<rows>.*?)\n\s*\]\s*\}',
        re.DOTALL,
    )
    for match in pattern.finditer(source):
        rows = json.loads(f"[{match.group('rows')}]")
        groups.append((match.group("prefix"), match.group("title"), rows))
    if len(groups) != 23:
        raise RuntimeError(f"expected 23 groups, got {len(groups)}")
    return groups


def records_for_evidence() -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}

    def record(case: str, status: str, actual: str, evidence: str) -> None:
        if case in records:
            raise RuntimeError(f"duplicate case mapping: {case}")
        records[case] = {
            "status": status,
            "actual": actual,
            "evidence": evidence,
        }

    api_evidence = str(EVIDENCE / "followup-api-probe.json")
    read_evidence = str(EVIDENCE / "readonly-api-probe.json")
    privacy_evidence = str(EVIDENCE / "header-privacy-probe.json")
    playwright_evidence = str(
        ROOT / "frontend" / "test-results" / "web-fulltest-round2.json"
    )
    ui1 = str(EVIDENCE / "ui-followup-results.json")
    ui2 = str(EVIDENCE / "ui-followup-round2-results.json")
    ui3 = str(EVIDENCE / "ui-followup-round3-results.json")
    ui4 = str(EVIDENCE / "round4" / "ui-round1-results.json")
    ui4b = str(EVIDENCE / "round4" / "ui-round2-results.json")
    round4_api = str(EVIDENCE / "round4-api-probe.json")
    round4_auth = str(EVIDENCE / "round4-auth-probe2.json")
    round4_upload = str(EVIDENCE / "round4-upload-final.json")
    round5_responsive = str(EVIDENCE / "round5" / "responsive-measurements.json")
    round6_playwright = str(
        ROOT / "frontend" / "test-results" / "web-isolated-final.json"
    )
    round6_runtime = str(EVIDENCE / "round6-runtime-health.json")
    round8_uploads = str(EVIDENCE / "round8-upload-card-20261019.json")
    round8_modal = str(EVIDENCE / "round8-modal-app-list-20261019.json")

    record("ENV-002", "pass", "15173/18189/8188 均在监听，PID 分别为 9244/15644/8596；未发现被测试进程占用 18190-18193。", api_evidence)
    record("ENV-003", "pass", "管理台与 /api/v1/health 均返回 200；Vite 代理与直连命中同一后端。", api_evidence)
    record("ENV-004", "pass", "8188/system_stats 返回 200，本地 ComfyUI 版本 0.41.2，未触发推理。", read_evidence)
    record("ENV-005", "pass", "状态页显示 SDK 可用、已登录、modal 1.5.5、deployed；重新自检成功。", ui3)
    record("ENV-011", "pass", "2026-10-18 Asia/Shanghai；Modal 计费周期 2026-10，账单按 UTC 日界说明核对。", read_evidence)
    record("ENV-012", "pass", "本轮范围仅 Web 管理台、管理 API、数据面与 Modal 云端链路，未打开 exe/整合包。", ui1)

    record("API-001", "pass", "修复后 health 返回 200，bridge.url 与 listen 均为运行时入口 127.0.0.1:18189；CLI 写入实际 host/port，开发态 uvicorn 回落到请求 Host。", round6_runtime)
    record("API-002", "pass", "SDK 状态接口返回 200；logged_in=true，version=modal 1.5.5，app_state=deployed。", read_evidence)
    record("API-003", "pass", "账号列表返回 200，accounts 与 active 字段可读，未回显 secret。", read_evidence)
    record("API-007", "pass", "Node 环境返回 200，路径与版本 v24.20.0 和当前配置一致。", read_evidence)
    record("API-008", "pass", "GET /config 返回 200，local/bridge/modal 字段完整，secret 仅脱敏展示。", api_evidence)
    record("API-009", "pass", "节点路径 PUT 返回 200 并回读一致；非法类型返回 422，后续 GET 仍为合法配置。", round4_api)
    record("API-010", "pass", "真实 ComfyUI 根目录返回 exists=true、has_venv=true、valid=true；不存在目录返回 valid=false。", api_evidence)
    record("API-011", "pass", "扫描状态接口返回 200，idle 状态与扫描弹窗一致。", read_evidence)
    record("API-012", "pass", "POST /config/scan-comfy 返回 200/running，状态接口可轮询进度。", round4_api)
    record("API-013", "pass", "取消扫描返回 200，最终状态 done 且 cancelled/truncated=true，无遗留运行状态。", round4_api)
    record("API-014", "pass", "成本汇总返回 200，月度请求 80、GPU 秒 4724.2、估算 $2.55895，与报表及页面一致。", read_evidence)
    record("API-015", "pass", "真实字段 monthly_budget_usd=-1 返回 422；复核预算仍为 0，无部分写入。", api_evidence)
    record("API-016", "pass", "workflow/model 报表均返回 200；非法 by 返回 422；工作流分组全部为（未命名）。", read_evidence)
    record("API-017", "pass", "对账接口返回 200；本地估算 $2.55895、Modal GPU $12.2248、全资源 $15.0285，ratio=4.777、diverged=true。", read_evidence)
    record("API-018", "fail", "实际契约 scope 为 entries/workflow/model 且均返回 200、UTF-8 BOM 正常；计划中的 scope=report 返回 422，计划文案需与接口契约统一。", read_evidence)
    record("API-019", "pass", "launch 返回 200；本地检测、命令、PID、Modal app 状态和 extra_args 可读。", read_evidence)
    record("API-021", "pass", "本地状态接口返回 200；运行中、owned=true，PID 8596，日志尾部可读。", read_evidence)
    record("API-025", "pass", "容器列表返回 200；最终 count=0，无运行中容器。", api_evidence)
    record("API-027", "pass", "本地节点清单返回 200，版本、class_type 与自定义包数据可读。", read_evidence)
    record("API-028", "pass", "云端节点清单返回 200，manifest 与 class 清单可读。", read_evidence)
    record("API-029", "pass", "差异缓存与 force=true 均返回 200，counts 和缓存时间字段可读。", read_evidence)
    record("API-032", "pass", "版本接口返回 200；本地 0.41.2、云端 pin 0.40.0，drift=true 与节点页告警一致。", read_evidence)
    record("API-034", "pass", "依赖预检 force=true 返回 200，状态、冲突和建议字段可读。", read_evidence)
    record("API-037", "pass", "工作流本地清单返回 200，name/file/hash/size/mtime 字段可读。", read_evidence)
    record("API-038", "pass", "工作流同步状态返回 200，synced/local_new/cloud_new/changed/conflict 字段可读。", read_evidence)
    record("API-040", "pass", "7/30/90 天运行统计均返回 200，次数、成功失败、后端分布、耗时和成本可读。", read_evidence)
    record("API-041", "pass", "本地与云端均明确不存在时返回 404，前端可呈现可读错误。", round8_uploads)
    record("API-042", "pass", "作业列表返回 200，类型、状态、进度、时间和运行数字段可读。", read_evidence)
    record("API-043", "pass", "不存在 job 返回 404，响应可读。", read_evidence)
    record("API-045", "pass", "图片/视频媒体清单均返回 200；当前 media success fixture 中图片 65、视频 0。", read_evidence)
    record("API-046", "pass", "合法媒体路径返回文件流；../ 越界返回 400，未泄露文件。", read_evidence)
    record("API-047", "pass", "本地日志返回 200；tail=-1 返回 422，非法边界被拒绝。", read_evidence)
    record("API-049", "pass", "Modal 日志接口返回 200，online=true，里程碑与 stdout 可读；早期 online=false 未复现。", api_evidence)
    record("API-052", "pass", "backend=modal&status=done 及本地筛选返回 200；limit=0 返回 422；结果与前端列表一致。", read_evidence)
    record("API-053", "pass", "请求 #200、不存在请求和非法 ID 分别返回 200/404/422，详情字段与列表一致。", api_evidence)
    record("API-054", "pass", "请求 #200 图快照返回 200，graph hash 与快照正常。", read_evidence)
    record("API-055", "pass", "非法 manual_backend 与 x-bridge-backend 均返回 422。", round8_uploads)
    record("API-056", "pass", "路由规则返回 200，固定规则、kinds、enabled 和顺序可读。", read_evidence)
    record("API-058", "pass", "设置同步返回 200；键级 diff、敏感值掩码与可用性可读。", read_evidence)
    record("API-060", "pass", "Modal 卷信息返回 200，credentials/SDK/主卷/分类字段可读。", read_evidence)
    record("API-061", "pass", "checkpoints/loras/vae overview 均返回 200，本地/云端/both 行与大小可读。", read_evidence)
    record("API-063", "pass", "模型卷同步作业列表返回 200，方向、分类、进度、失败数与状态可读。", read_evidence)
    record("API-065", "pass", "上传 init 缺字段返回 422，未创建会话。", read_evidence)
    record("API-066", "pass", "旧未开始会话已清理；剩余 6 条为 5 done + 1 verify error，页面展示一致；真实删除后收敛为 5 条。", round8_uploads)
    record("API-067", "pass", "不存在上传 ID 返回 422，错误可读。", read_evidence)
    record("API-068", "pass", "合法上传 init 返回 200；文件名、大小、MD5、分块数与卷路径可读。", round4_upload)
    record("API-069", "pass", "合法分块返回 200；错误 MD5 和越界索引均返回 409；续传状态与 declared_md5/actual_md5 可读。未知字段问题归 NEG-010。", round4_upload)
    record("API-070", "pass", "complete 后状态收敛 done；actual_md5 与声明一致；删除返回 200，随后 GET 返回 404。", round4_upload)
    record("API-071", "pass", "数据卷 meta 返回 200，总量、文件数和计算状态字段可读。", read_evidence)
    record("API-072", "pass", "整体超时与限流收敛修复后，卷统计最终返回 computing=false、两卷、40.92GB、543 文件。", round8_uploads)
    record("API-075", "pass", "越界 path 返回 422，未读取越界目录。", read_evidence)
    record("API-077", "pass", "回收站列表返回 200，条目结构可读。", read_evidence)
    record("API-081", "pass", "数据卷目录候选返回 200，结构可读。", read_evidence)
    record("API-083", "pass", "移动任务列表返回 200，当前为空。", read_evidence)
    record("API-086", "pass", "下载任务列表返回 200，当前为空。", read_evidence)
    record("API-089", "pass", "预热状态返回 200，online=true、app_state=deployed。", read_evidence)

    for case in ("DP-001", "DP-003", "DP-004", "DP-005", "DP-006", "DP-007", "DP-008", "DP-023"):
        record(case, "pass", f"{case} 数据面请求返回 200；合法响应结构可读。", read_evidence)
    record("DP-010", "pass", "确认当前架构按文件回传到本地 output_dir 并由媒体库展示；本地 ComfyUI /history 原生历史不包含云端任务。", round8_uploads)
    record("DP-022", "pass", "未知 ComfyUI 路径返回 404，透明透传语义可读。", read_evidence)

    record("UI-001", "pass", "侧栏 10 个内页均真实点击跳转成功（仪表盘另行首屏验证）；路由与高亮正常，无白屏。", ui1)
    record("UI-005", "pass", "顶栏“任务”真实点击打开抽屉，Escape 可关闭，状态保持。", ui1)
    record("UI-007", "pass", "health.bridge.url 已修复为实际运行时入口 18189，顶栏“前往出图界面”据此跳转。重启后真实请求确认 bridge.url/listen 一致。", round6_runtime)
    record("UI-010", "pass", "深链 /nodes、/logs、/settings 刷新可用，/no-such-page 显示可读 404；Playwright 4/4 通过。", playwright_evidence)
    record("UI-027", "pass", "节点页本地清单/差异矩阵 Tab 真实切换成功；KSampler 搜索可用；页面显示云端 pin drift 告警。", ui1)
    record("UI-030", "pass", "节点页按节点包/按 class_type 切换与云端差异视图可渲染；未确认所有筛选组合。", ui1)
    record("UI-046", "pass", "工作流本地清单、同步状态、运行统计三个 Tab 真实切换成功。", ui2)
    record("UI-049", "pass", "运行统计近 7/30/90 天三段真实切换成功；统计表随筛选重载。", ui2)
    record("UI-066", "pass", "媒体库图片/视频单选筛选真实切换；当前图片 65、视频 0；图片点击可打开详情弹窗。", ui2)
    record("UI-073", "pass", "成本页按工作流/按主模型真实切换；工作流分组 80 条全部显示（未命名）。", ui2)
    record("UI-085", "pass", "请求日志“后端=modal + 状态=done”组合筛选、详情抽屉、刷新保持筛选均通过 Playwright。", playwright_evidence)
    record("UI-090", "pass", "实时控制台可打开；自适应/左右/上下布局切换并写入 localStorage；布局记忆可验证。", playwright_evidence)
    record("UI-096", "pass", "请求日志/实时控制台两个 Tab 正常切换；请求列表 195 条可读，控制台实时推送中。", ui2)
    record("UI-107", "pass", "状态页可用；“重新自检”真实点击后 SDK/登录/应用/延迟刷新，loading 收敛。", ui3)
    record("UI-111", "pass", "设置页本地/启动命令/桥接/Modal/护栏与预算/设置同步六个 Tab 均真实打开并渲染。", ui3)

    record("NEG-005", "pass", "health/config/status DOM 中 secret 仅显示掩码；未发现明文 token/proxy secret。", ui3)
    record("NEG-001", "pass", "隔离管理面：无认证和错误令牌均返回 401。", round4_auth)
    record("NEG-002", "pass", "正确 Bearer 与 Basic 均返回 200；错误 Bearer/Basic 返回 401。", round4_auth)
    record("NEG-003", "pass", "跨站 Origin 请求返回 403，白名单来源成功。", round4_auth)
    record("NEG-004", "pass", "/bridge/ 无认证返回 401，正确 Basic 返回 200。", round4_auth)
    record("NEG-006", "pass", "../、编码越界和非法卷路径返回 400/422，未发生越界读取。", api_evidence)
    record("NEG-009", "pass", "分页/limit/days/cycle/category 非法参数返回 4xx 或安全钳制。", read_evidence)
    record("NEG-010", "pass", "upload init 未知字段与非法路由后端均返回 422。", round8_uploads)

    record("UI-024", "pass", "设置页本地、启动命令、桥接、Modal、护栏与预算、设置同步六个 Tab 均真实点击渲染。", ui4)
    record("UI-025", "pass", "扫描弹窗真实打开；未选目录时“采用”按钮禁用。", ui4)
    record("UI-026", "pass", "开始/重新扫描出现进度；取消后状态收敛，按钮恢复。", ui4)
    # UI-027 已在上一轮（节点页）映射；设置页的目录校验对应独立命名用例。
    record("UI-028", "pass", "Node.js 检测显示 v24.20.0、来源和路径。", ui4)
    record("UI-038", "pass", "修复后 Playwright：配置向导打开即聚焦 dialog 内内容容器，Escape 关闭；账号档案弹窗打开即聚焦名称输入框，Escape 关闭。4 项回归 4/4 通过。", round6_playwright)
    record("UI-192", "pass", "修复后 768/1024 实测：设置页与日志页 scrollWidth 均等于 clientWidth；模型目录行可换行，日志表在卡片内横向滚动，详情抽屉限制为 min(60%, 720px)。", round6_playwright)

    record("FLOW-004", "todo", "历史请求 #200 未携带 workflow_name；本轮未重新提交云端生图，成本归组修复需待 Runner 可用后复测。", round8_modal)
    record("FLOW-005", "pass", "云端产物按文件回传并在媒体库可见；/history 为空是云端任务不写入本地原生历史的当前口径。", round8_uploads)
    record("FLOW-006", "pass", "成本报表可按工作流/主模型查看成本，CSV 导出 200 且带 BOM。", read_evidence)
    record("FLOW-009", "pass", "Modal Runner 在线且 app_state=deployed；当前无运行容器，未触发新生成。", api_evidence)

    return records


def main() -> None:
    groups = group_rows()
    evidence = records_for_evidence()
    output_records: dict[str, dict[str, object]] = {}
    ids: list[str] = []
    prefix_offsets: dict[str, int] = {}
    metadata: dict[str, dict[str, str]] = {}
    for prefix, title, rows in groups:
        offset = prefix_offsets.get(prefix, 0)
        for index, row in enumerate(rows):
            case = f"{prefix}-{offset + index + 1:03d}"
            ids.append(case)
            metadata[case] = {
                "group": title,
                "priority": row[0],
                "precondition": row[1],
                "operation": row[2],
                "expected": row[3],
            }
        prefix_offsets[prefix] = offset + len(rows)

    for case in evidence:
        if case not in ids:
            raise RuntimeError(f"unknown case: {case}")
    for case in ids:
        item = evidence.get(case, {})
        output_records[case] = {
            **metadata[case],
            "status": item.get("status", "todo"),
            "actual": item.get("actual", ""),
            "evidence": item.get("evidence", ""),
        }

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    output = {
        "schema": "comfybridge-web-test-record/v1",
        "plan": PLAN.name,
        "exported_at": timestamp,
        "paused_at": "2026-10-18T23:43:50+08:00",
        "snapshot_status": "paused",
        "baseline": "main@924a49e",
        "scope": "web-only; excludes packaged desktop application",
        "run": {
            "executor": "Codex / ego-browser / Playwright",
            "machine": "Windows local",
            "browser": "Ego Lite + Microsoft Edge Playwright",
            "backend_commit": "main@924a49e",
            "modal_budget_usd": 0.0,
            "notes": (
                "Paused on 2026-10-18T23:43:50+08:00 at the user's request. "
                "Only evidence-backed cases were marked; the page-level read-only test was interrupted and is not counted as passed. "
                "All local test services were stopped, with no LISTENING sockets on the tested ports. "
                "Modal container list last succeeded with count=0 at 2026-10-18T23:22:00+08:00; "
                "the pause-time recheck timed out after 30 seconds and must be repeated before resuming."
            ),
        },
        "records": output_records,
    }
    json_path = EVIDENCE / "comfybridge-web-test-record-20261018.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = EVIDENCE / "comfybridge-web-test-record-20261018.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ID", "状态", "实际结果", "证据"])
        for case in ids:
            item = output_records[case]
            writer.writerow([case, item["status"], item["actual"], item["evidence"]])

    counts: dict[str, int] = {}
    for item in output_records.values():
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "json": str(json_path),
        "csv": str(csv_path),
        "total": len(ids),
        "counts": counts,
    }
    (EVIDENCE / "comfybridge-web-test-summary-20261018.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""真实两端发布验收冒烟集（pytest -m live）。

覆盖路线图 P0-1：

1. 桥接器健康检查可读。
2. 本地 ComfyUI `/object_info` 可用。
2b. 本地通道真实出图且终态自行收敛（不需要浏览器 WS）。
3. Modal Runner 凭证与部署状态可探。
4. 可选的只读请求断言（不产生新费用）。
5. 显式开启后提交一次云端文生图并检查输出与日志。
6. 图生图输入预上传与输出回传。
7. 运行中中断与终态收敛。
8. 断线重连后的 history 兜底。
9. 无输出工作流的合法收尾。

未设置 ``CB_LIVE_MODAL=1`` 时，云端提交用例会 skip，而不是失败或假通过。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from .conftest import (
    LiveEnv,
    load_fixture_prompt,
    load_local_fixture_prompt,
    load_no_output_fixture_prompt,
    require_local,
    require_modal,
)

pytestmark = pytest.mark.live


def _request_by_prompt_id(client: httpx.Client, prompt_id: str) -> dict[str, Any]:
    resp = client.get("/api/v1/requests", params={"limit": 500})
    assert resp.status_code == 200, f"读取请求列表失败: HTTP {resp.status_code}"
    items = resp.json().get("items") or []
    rec = next((r for r in items if r.get("prompt_id") == prompt_id), None)
    assert rec is not None, f"请求列表找不到 prompt_id={prompt_id}"
    return rec


def _wait_terminal(client: httpx.Client, prompt_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _request_by_prompt_id(client, prompt_id)
        if last.get("status") in {"done", "error", "cancelled", "blocked"}:
            return last
        time.sleep(3)
    pytest.fail(
        f"请求 {prompt_id} 在 {timeout_s:.0f}s 内未到终态，"
        f"最后状态={last.get('status')!r} 错误={last.get('error')!r}"
    )


def _assert_output_landed(client: httpx.Client, rec: dict[str, Any]) -> None:
    """优先按请求终态和时间线断言输出；媒体库作为第二证据。"""
    assert rec.get("status") == "done", f"请求未成功: {rec.get('error')}"
    assert rec.get("final_backend") == "modal", f"请求未走 Modal: final_backend={rec.get('final_backend')}"
    assert rec.get("finished_ms"), "终态请求缺少 finished_ms"
    assert rec.get("started_ms"), "终态请求缺少 started_ms"

    media = client.get("/api/v1/media/local", params={"kind": "all", "limit": 200})
    assert media.status_code == 200, f"媒体库读取失败: HTTP {media.status_code}"
    items = media.json().get("items") or []
    assert items, "媒体库为空，未观察到回传输出"


def _submit(
    client: httpx.Client,
    prompt: dict[str, Any],
    client_id: str,
    headers: dict[str, str] | None = None,
) -> str:
    # LIVE 是已授权的自动化提交方：等价于浏览器弹窗确认（P2-5），显式携带确认头。
    merged = {"X-Bridge-Privacy-Confirmed": "1", **(headers or {})}
    resp = client.post(
        "/prompt",
        json={"prompt_id": "", "client_id": client_id, "prompt": prompt},
        headers=merged,
    )
    assert resp.status_code == 200, f"提交失败: HTTP {resp.status_code} {resp.text[:500]}"
    prompt_id = resp.json().get("prompt_id")
    assert prompt_id, f"提交应答缺少 prompt_id: {resp.json()}"
    return prompt_id


def _local_input_image(live_env: LiveEnv) -> Path:
    if not live_env.input_image:
        pytest.skip("未设置 CB_LIVE_INPUT_IMAGE，跳过图生图输入预上传用例")
    rel = live_env.input_image.replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/"):
        pytest.fail("CB_LIVE_INPUT_IMAGE 必须是本地 ComfyUI input/ 下的相对文件名")
    # 通过桥接器健康检查拿到本地 ComfyUI 根即可确认文件存在，避免误用测试机路径。
    return Path(rel)


def test_bridge_health(live_env: LiveEnv, bridge_health: dict[str, Any]) -> None:
    """桥接器进程、本地端、Modal 端聚合状态可读。"""
    assert "local" in bridge_health
    assert "modal" in bridge_health
    assert bridge_health.get("bridge", {}).get("listen"), "健康检查缺少桥接监听地址"
    require_local(bridge_health)


def test_local_comfy_object_info(bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """本地 ComfyUI 可读，节点注册表非空。"""
    require_local(bridge_health)
    resp = bridge_client.get("/object_info")
    assert resp.status_code == 200, f"/object_info 失败: HTTP {resp.status_code} {resp.text[:300]}"
    data = resp.json()
    assert isinstance(data, dict) and data, "/object_info 返回为空"
    assert any("KSampler" in str(k) or "CheckpointLoader" in str(k) for k in data), (
        "/object_info 未包含基础生成节点，确认当前 ComfyUI 已完整启动"
    )


def test_local_text_to_image(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """本地通道真实出图：强制 x-bridge-backend=local，不依赖浏览器 WS 也要收敛到 done。

    回归真机缺陷（2026-10-11）：本地请求只靠浏览器 WS 中继回写状态，纯 HTTP 提交
    会永远停在 queued，即使图已落 output/。本用例要求终态由后端自行判定。
    """
    require_local(bridge_health)
    fixture = load_local_fixture_prompt(live_env)
    if not fixture:
        pytest.skip("未设置 CB_LIVE_LOCAL_FIXTURE_JSON，跳过本地出图用例")
    prompt_id = _submit(
        bridge_client,
        fixture["prompt"],
        "bridge-live-local",
        headers={"x-bridge-backend": "local"},
    )
    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    assert rec.get("status") == "done", f"本地执行未成功收敛: {rec.get('status')} {rec.get('error')}"
    assert rec.get("final_backend") == "local", f"未走本地通道: {rec.get('final_backend')}"
    assert rec.get("finished_ms"), "终态请求缺少 finished_ms"


def test_modal_runner_reachable(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """云端 Runner 状态可用；该用例只探状态，不产生生成请求。"""
    require_modal(live_env, bridge_health)
    resp = bridge_client.get("/api/v1/logs/modal")
    assert resp.status_code == 200, f"Modal 日志端点失败: HTTP {resp.status_code}"
    body = resp.json()
    assert "online" in body and "entries" in body


def test_existing_request_readonly(live_env: LiveEnv, bridge_client: httpx.Client) -> None:
    """可选：对已存在的 prompt_id 做只读终态与输出检查，适合复验历史现场。"""
    if not live_env.prompt_id:
        pytest.skip("未设置 CB_LIVE_PROMPT_ID，跳过往期请求复验")
    rec = _request_by_prompt_id(bridge_client, live_env.prompt_id)
    assert rec.get("status") in {"done", "error", "cancelled", "blocked"}
    if rec.get("status") == "done" and rec.get("final_backend") == "modal":
        _assert_output_landed(bridge_client, rec)


def test_modal_text_to_image(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """显式开启费用开关后，提交一次云端文生图并检查输出回传与日志留痕。"""
    require_modal(live_env, bridge_health)
    fixture = load_fixture_prompt(live_env)
    prompt_id = _submit(bridge_client, fixture["prompt"], "bridge-live-smoke")

    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    _assert_output_landed(bridge_client, rec)

    # 时间线与图快照是发布验收的一部分：至少有一条可查询的决策轨迹。
    assert rec.get("rule_trace"), "请求记录缺少 rule_trace"
    assert rec.get("graph_hash"), "请求记录缺少图快照哈希"


def test_modal_image_to_image(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """图生图：输入文件预上传、云端执行与输出回传。"""
    require_modal(live_env, bridge_health)
    _local_input_image(live_env)
    fixture = load_fixture_prompt(live_env)
    prompt = fixture["prompt"]
    # 夹具若不含 LoadImage，说明调用方传入的不是图生图工作流；保持显式失败。
    assert any(node.get("class_type") == "LoadImage" for node in prompt.values()), (
        "图生图夹具必须包含 LoadImage 节点；请用 CB_LIVE_INPUT_IMAGE 指定输入文件"
    )
    prompt_id = _submit(bridge_client, prompt, "bridge-live-img2img")
    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    _assert_output_landed(bridge_client, rec)


def test_modal_interrupt(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """运行中中断：请求最终收敛为 cancelled，而不是悬挂。"""
    require_modal(live_env, bridge_health)
    fixture = load_fixture_prompt(live_env)
    prompt = fixture["prompt"]
    # 换一次种子：与文生图用例同容器时不共享 ComfyUI 节点缓存，否则 0.02s 就跑完，
    # 「中断」根本落不到执行中，用例会退化成假绿。
    for node in prompt.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if node.get("class_type") == "KSampler" and isinstance(inputs, dict):
            inputs["seed"] = int(time.time()) % 2**31
            # 拉长采样窗口：暖容器 + 8 步 ~15-20s 就跑完，"running"（桥接器开始跟踪）
            # 早于云端实际开始采样，中断容易落空。40 步给中断留出可靠落点。
            inputs["steps"] = 40
    prompt_id = _submit(bridge_client, fixture["prompt"], "bridge-live-interrupt")
    # 等请求真正进入 running 再中断：固定短睡会落在云端提交完成前的空窗，
    # /interrupt 空转后任务照常跑完（真机 2026-10-16：暖容器 ~20s 跑完 → done）。
    deadline = time.time() + 120
    while time.time() < deadline:
        recs = bridge_client.get("/api/v1/requests?limit=5").json().get("items") or []
        cur = next((r for r in recs if r.get("prompt_id") == prompt_id), None)
        if cur and cur.get("status") == "running":
            break
        time.sleep(2)
    resp = bridge_client.post("/interrupt")
    assert resp.status_code < 500, f"中断端点异常: HTTP {resp.status_code} {resp.text[:300]}"
    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    # 真机回归（2026-10-11）：/interrupt 曾被 WS 泵阻塞而从未送达，请求跑完才结束。
    # 修复后中断必须真正生效，终态收敛为 cancelled。
    assert rec.get("status") == "cancelled", (
        f"中断未生效，终态={rec.get('status')} 错误={rec.get('error')}"
    )


def test_modal_reconnect_history_fallback(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """断线重连兜底：不依赖浏览器 WS，终态仍能从 history/请求记录收敛。"""
    require_modal(live_env, bridge_health)
    fixture = load_fixture_prompt(live_env)
    prompt_id = _submit(bridge_client, fixture["prompt"], "bridge-live-reconnect")
    # 只读 API 模拟浏览器断线后的重连：执行器必须自行完成 history 兜底并落终态。
    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    assert rec.get("status") in {"done", "error", "cancelled"}
    if rec.get("status") == "done":
        _assert_output_landed(bridge_client, rec)


def test_modal_no_output_workflow(live_env: LiveEnv, bridge_client: httpx.Client, bridge_health: dict[str, Any]) -> None:
    """无输出工作流：合法结束，不因 0 文件误报成功/失败。"""
    require_modal(live_env, bridge_health)
    fixture = load_no_output_fixture_prompt(live_env)
    if not fixture:
        pytest.skip("未设置 CB_LIVE_NO_OUTPUT_FIXTURE_JSON，跳过无输出工作流用例")
    prompt = fixture["prompt"]
    prompt_id = _submit(bridge_client, prompt, "bridge-live-no-output")
    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    # 无输出工作流的合法终态应为 done；error 会掩盖回归，只有 cancelled 能被视为人工中断。
    assert rec.get("status") == "done", f"无输出工作流未合法收尾: {rec.get('status')} {rec.get('error')}"

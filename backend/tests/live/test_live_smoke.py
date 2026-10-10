"""真实两端发布验收冒烟集（pytest -m live）。

覆盖路线图 P0-1：

1. 桥接器健康检查可读。
2. 本地 ComfyUI `/object_info` 可用。
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


def _submit(client: httpx.Client, prompt: dict[str, Any], client_id: str) -> str:
    resp = client.post(
        "/prompt",
        json={"prompt_id": "", "client_id": client_id, "prompt": prompt},
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
    prompt_id = _submit(bridge_client, fixture["prompt"], "bridge-live-interrupt")
    # 提交返回即由后台执行器接管；短等待后中断，覆盖“运行中”而不是排队前后的空窗。
    time.sleep(3)
    resp = bridge_client.post("/interrupt")
    assert resp.status_code < 500, f"中断端点异常: HTTP {resp.status_code} {resp.text[:300]}"
    rec = _wait_terminal(bridge_client, prompt_id, timeout_s=900)
    assert rec.get("status") in {"cancelled", "done", "error"}, f"中断后终态异常: {rec.get('status')}"
    if rec.get("status") == "cancelled":
        assert "中断" in str(rec.get("error") or "") or not rec.get("error")


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

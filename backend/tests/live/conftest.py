"""live 联调测试的环境开关与共享夹具。

本文件不读取用户真实配置，只把显式环境变量转成测试输入：

- ``CB_LIVE=1``：总开关；未设置时所有 live 用例 skip。
- ``CB_BRIDGE_URL``：桥接器管理面地址，默认 ``http://127.0.0.1:8189``。
- ``CB_LIVE_MODAL=1``：允许执行会产生 Modal 费用的云端用例。
- ``CB_LIVE_FIXTURE_JSON``：可选，自定义文生图工作流 JSON 文件路径。
- ``CB_LIVE_PROMPT_ID``：可选，复用已有请求做只读断言，不提交新任务。

设计原则：

1. 缺环境只 skip，绝不把 mock 当作 live 通过。
2. 默认不产生费用；只有显式 ``CB_LIVE_MODAL=1`` 才提交云端生成。
3. 凭证、路径与工作流不进测试输入，用户工作流只从 ``CB_LIVE_FIXTURE_JSON`` 读取。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

_DEFAULT_BRIDGE = "http://127.0.0.1:8189"
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LiveEnv:
    """一次 live 运行的环境快照。"""

    bridge_url: str
    modal_enabled: bool
    fixture_path: Path | None
    prompt_id: str | None


@pytest.fixture(scope="session")
def live_env() -> LiveEnv:
    if not _env_flag("CB_LIVE"):
        pytest.skip("未设置 CB_LIVE=1，跳过真实两端联调用例")

    bridge = os.environ.get("CB_BRIDGE_URL", _DEFAULT_BRIDGE).strip().rstrip("/")
    if not bridge.startswith(("http://", "https://")):
        pytest.fail("CB_BRIDGE_URL 必须是 http(s) URL")

    fixture = os.environ.get("CB_LIVE_FIXTURE_JSON", "").strip()
    fixture_path = Path(fixture).expanduser().resolve() if fixture else None
    if fixture_path is not None and not fixture_path.is_file():
        pytest.fail(f"CB_LIVE_FIXTURE_JSON 指向的文件不存在: {fixture_path}")

    return LiveEnv(
        bridge_url=bridge,
        modal_enabled=_env_flag("CB_LIVE_MODAL"),
        fixture_path=fixture_path,
        prompt_id=os.environ.get("CB_LIVE_PROMPT_ID", "").strip() or None,
    )


@pytest.fixture
def bridge_client(live_env: LiveEnv):
    """同步 HTTP 客户端：live 用例保持简单，避免把事件循环问题引入发布验收。"""
    with httpx.Client(base_url=live_env.bridge_url, timeout=_DEFAULT_TIMEOUT) as client:
        yield client


@pytest.fixture
def bridge_health(bridge_client: httpx.Client) -> dict[str, Any]:
    resp = bridge_client.get("/api/v1/health")
    assert resp.status_code == 200, f"桥接器健康检查失败: HTTP {resp.status_code} {resp.text[:300]}"
    return resp.json()


def require_local(bridge_health: dict[str, Any]) -> None:
    local = bridge_health.get("local") or {}
    if not local.get("online"):
        pytest.skip(f"本地 ComfyUI 未连接：{local.get('error') or local.get('url')}")


def require_modal(live_env: LiveEnv, bridge_health: dict[str, Any]) -> None:
    if not live_env.modal_enabled:
        pytest.skip("未设置 CB_LIVE_MODAL=1，跳过会产生云端费用的用例")
    modal = bridge_health.get("modal") or {}
    if not modal.get("online"):
        pytest.skip(f"Modal Runner 未就绪：{modal.get('reason')}")


def load_fixture_prompt(live_env: LiveEnv) -> dict[str, Any]:
    """读取自定义工作流；未提供时返回内置的最小可选夹具。

    内置夹具只做结构校验，不保证用户的模型名可用。真正提交云端前，
    用例会要求通过 ``CB_LIVE_FIXTURE_JSON`` 提供可在当前环境运行的工作流。
    """
    if live_env.fixture_path is not None:
        try:
            data = json.loads(live_env.fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            pytest.fail(f"读取 CB_LIVE_FIXTURE_JSON 失败: {exc}")
        if not isinstance(data, dict) or "prompt" not in data or not isinstance(data["prompt"], dict):
            pytest.fail("CB_LIVE_FIXTURE_JSON 必须是 ComfyUI API 工作流对象（顶层含 prompt 字典）")
        return data

    # 最小结构夹具：只用于显式只读检查，不用于付费提交。
    return {
        "prompt": {
            "1": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 64, "height": 64, "batch_size": 1},
            }
        }
    }

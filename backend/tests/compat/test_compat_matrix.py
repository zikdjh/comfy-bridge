"""兼容性矩阵测试（规划 §10；§9 风险「ComfyUI API/前端协议快速演进」的对策落地）。

夹具自上游源码逐条提取（提取点与版本源码指纹见 tests/compat/manifest.json），
把桥接器协议消费代码对每个锁定版本逐夹具断言——上游协议漂移时，把新版本夹具
目录一放、manifest 指纹一更，失败项即漂移点：

  提交应答    runner/client.post_prompt（prompt_id 提取 / 400 拒绝上浮）
  WS 事件泵   runner/executor._ws_pump（转发 + prompt_id 映射 + 终态捕捉 + 他任务过滤）
  history     runner/client.get_history 解包 + executor._classify_entry 三态分类
  输出回传    executor._download_outputs（images/gifs/video/audio 四桶 + 子目录 + 越界拒绝）
  容器日志    runner/client.fetch_container_logs → normalize_entry（/internal/logs/raw 形状）
  object_info 顶层键集（diff 矩阵 class_types 的唯一消费面）
  本地 tap    proxy/ws_relay._tap_event（本地 WS 事件 → 请求状态，与云端同代协议）
  双通道跟踪  executor._track（WS 快路径终态 / history 权威兜底）
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.core.config import AppConfig, LocalConfig, ModalConfig
from app.proxy import ws_relay as wsm
from app.runner import client as rc_mod
from app.runner import executor as ex_mod

# 两个近期版本 + M0-3 参照过的 v0.37.2（三版 execution.py 同哈希；server.py 仅差资产导入守卫）
VERSIONS = ["v0.37.2", "v0.38.0", "v0.38.2"]
FX_ROOT = Path(__file__).parent / "fixtures" / "comfyui"
CLOUD_PID = "0f0e0d0c-1111-2222-3333-444455556666"
BRIDGE_PID = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"


def _fx(version: str, name: str) -> dict:
    return json.loads((FX_ROOT / version / name).read_text(encoding="utf-8"))


def _settings(**kw) -> AppConfig:
    return AppConfig(
        local=LocalConfig(output_dir=kw.get("output_dir", "out")),
        modal=ModalConfig(web_url="http://runner.test", task_timeout=kw.get("task_timeout", 3600)),
    )


def _mock_transport(monkeypatch, routes: dict) -> None:
    """把 runner.client 的 HTTP 指向 MockTransport；routes = {路径前缀: handler}。"""

    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, fn in routes.items():
            if request.url.path == prefix or request.url.path.startswith(prefix):
                return fn(request)
        return httpx.Response(404, json={"error": f"unexpected path {request.url.path}"})

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(660.0, connect=15.0))

    monkeypatch.setattr(rc_mod, "_client", factory)


def _boom(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("boom")


class _FakeConn:
    """websockets.connect 替身：按给定帧序列产出（握手参数已由 test_runner_auth 钉住）。"""

    def __init__(self, frames: list):
        self._frames = frames

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            for f in self._frames:
                yield f

        return _gen()


def _frames_of(version: str, name: str, with_binary: bool = False) -> list:
    frames = [json.dumps(e, ensure_ascii=False) for e in _fx(version, name)]
    frames.append("{not-json")  # 非 JSON 文本帧：应被静默忽略
    if with_binary:
        frames.insert(2, b"\x00\x01binary-preview")  # 二进制预览帧：不回传
    return frames


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(ex_mod.console, "log", lambda *a, **k: None)


# ---------------------------------------------------------------- 提交应答


@pytest.mark.parametrize("version", VERSIONS)
def test_prompt_ack_shape(version):
    ack = _fx(version, "prompt_ack.json")
    assert set(ack) == {"prompt_id", "number", "node_errors"}
    assert ack["prompt_id"] == CLOUD_PID and isinstance(ack["node_errors"], dict)
    # 桥接器只消费 prompt_id（executor: state["modal_pid"] = ack.get("prompt_id")）


@pytest.mark.parametrize("version", VERSIONS)
async def test_post_prompt_relays_ack_and_400(monkeypatch, version):
    ack = _fx(version, "prompt_ack.json")
    rej = _fx(version, "prompt_reject_400.json")
    monkeypatch.setattr(rc_mod, "get_settings", lambda: _settings())
    _mock_transport(monkeypatch, {"/prompt": lambda req: httpx.Response(400, json=rej)})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await rc_mod.post_prompt({"prompt_id": BRIDGE_PID})
    _mock_transport(monkeypatch, {"/prompt": lambda req: httpx.Response(200, json=ack)})
    assert (await rc_mod.post_prompt({"prompt_id": BRIDGE_PID}))["prompt_id"] == CLOUD_PID


# ---------------------------------------------------------------- WS 事件泵


@pytest.mark.parametrize("version", VERSIONS)
async def test_ws_pump_success_forward_and_terminal(monkeypatch, version):
    sent: list[dict] = []
    term: list[tuple[str, dict]] = []
    monkeypatch.setattr(ex_mod, "send_event", lambda cid, ev: sent.append(ev))
    monkeypatch.setattr(
        ex_mod.websockets, "connect",
        lambda *a, **k: _FakeConn(_frames_of(version, "ws_events_success.json", with_binary=True)),
    )
    await ex_mod._ws_pump("cid", CLOUD_PID, BRIDGE_PID, lambda t, d: term.append((t, d)))

    types = [e["type"] for e in sent]
    # 云端 executed 帧被泵丢弃是设计使然：桥接器在输出落盘后从 history 权威重建 executed
    # （浏览器收到 executed 即请求 /view，文件必须已就位）；末尾 executing{node:null} 为上游
    # main.py 任务收尾帧，正常转发。
    assert types == [
        "status", "execution_start", "executing", "progress", "execution_cached", "progress", "executing",
    ]
    assert sent[0]["data"] == _fx(version, "ws_events_success.json")[0]["data"]  # status 原样转发
    for ev in sent[1:]:
        assert ev["data"]["prompt_id"] == BRIDGE_PID  # 云端 pid → 桥接 pid
    assert sent[-1]["data"]["node"] is None
    assert all(e["type"] != "execution_success" for e in sent)  # 终态走 on_terminal 不走转发
    assert term == [("execution_success", {"prompt_id": BRIDGE_PID})]


@pytest.mark.parametrize("version", VERSIONS)
async def test_ws_pump_error_terminal(monkeypatch, version):
    sent: list[dict] = []
    term: list[tuple[str, dict]] = []
    monkeypatch.setattr(ex_mod, "send_event", lambda cid, ev: sent.append(ev))
    monkeypatch.setattr(
        ex_mod.websockets, "connect", lambda *a, **k: _FakeConn(_frames_of(version, "ws_events_error.json")),
    )
    await ex_mod._ws_pump("cid", CLOUD_PID, BRIDGE_PID, lambda t, d: term.append((t, d)))
    assert len(term) == 1
    etype, data = term[0]
    assert etype == "execution_error"
    assert data["exception_message"] == "CUDA out of memory."
    assert data["prompt_id"] == BRIDGE_PID
    assert all(e["type"] != "execution_error" for e in sent)


@pytest.mark.parametrize("version", VERSIONS)
async def test_ws_pump_interrupted_terminal(monkeypatch, version):
    term: list[tuple[str, dict]] = []
    monkeypatch.setattr(ex_mod, "send_event", lambda cid, ev: None)
    monkeypatch.setattr(
        ex_mod.websockets, "connect", lambda *a, **k: _FakeConn(_frames_of(version, "ws_events_interrupted.json")),
    )
    await ex_mod._ws_pump("cid", CLOUD_PID, BRIDGE_PID, lambda t, d: term.append((t, d)))
    etype, data = term[0]
    assert etype == "execution_interrupted"
    assert "exception_message" not in data  # 上游中断事件无该字段 → _track 兜底 detail ""
    assert data["prompt_id"] == BRIDGE_PID


# ---------------------------------------------------------------- history


@pytest.mark.parametrize("version", VERSIONS)
async def test_get_history_unwraps_entry(monkeypatch, version):
    body = _fx(version, "history_success.json")
    monkeypatch.setattr(rc_mod, "get_settings", lambda: _settings())
    _mock_transport(monkeypatch, {"/history/": lambda req: httpx.Response(200, json=body)})
    entry = await rc_mod.get_history(CLOUD_PID)
    assert entry["status"]["status_str"] == "success"
    assert entry["outputs"]["9"]["images"][0]["filename"] == "ComfyUI_00001_.png"
    # 未入历史 / 非 200 / 网络错误 → None（轮询兜底不炸）
    _mock_transport(monkeypatch, {"/history/": lambda req: httpx.Response(200, json={})})
    assert await rc_mod.get_history(CLOUD_PID) is None
    _mock_transport(monkeypatch, {"/history/": lambda req: httpx.Response(404, json={})})
    assert await rc_mod.get_history(CLOUD_PID) is None
    _mock_transport(monkeypatch, {"/history/": _boom})
    assert await rc_mod.get_history(CLOUD_PID) is None


@pytest.mark.parametrize("version", VERSIONS)
def test_classify_entry_three_states(version):
    ok = _fx(version, "history_success.json")[CLOUD_PID]
    assert ex_mod._classify_entry(ok) == ("success", "")
    kind, detail = ex_mod._classify_entry(_fx(version, "history_error.json")[CLOUD_PID])
    assert (kind, detail) == ("error", "CUDA out of memory.")
    intr = _fx(version, "history_interrupted.json")[CLOUD_PID]
    assert ex_mod._classify_entry(intr) == ("interrupted", "")
    # 缺 status 的条目 → error 兜底
    assert ex_mod._classify_entry({}) == ("error", "云端执行失败（无输出）")


# ---------------------------------------------------------------- 输出回传


@pytest.mark.parametrize("version", VERSIONS)
async def test_download_outputs_buckets_and_subfolder(monkeypatch, version, tmp_path):
    entry = _fx(version, "history_success.json")[CLOUD_PID]
    calls: list[tuple[str, str, str]] = []

    async def fake_view(filename, subfolder="", type_="output"):
        calls.append((filename, subfolder, type_))
        return b"data:" + filename.encode()

    monkeypatch.setattr(ex_mod.runner_client, "fetch_view", fake_view)
    monkeypatch.setattr(ex_mod.config, "get_settings", lambda: _settings(output_dir=str(tmp_path)))
    count = await ex_mod._download_outputs(entry["outputs"])
    assert count == 4
    assert ("ComfyUI_00001_.png", "", "output") in calls
    assert ("anim_00001_.gif", "", "output") in calls
    assert ("clip_00001.mp4", "sub/dir", "output") in calls
    assert ("take_00001_.flac", "", "audio") in calls
    assert (tmp_path / "ComfyUI_00001_.png").read_bytes() == b"data:ComfyUI_00001_.png"
    assert (tmp_path / "sub" / "dir" / "clip_00001.mp4").read_bytes() == b"data:clip_00001.mp4"


async def test_download_outputs_rejects_traversal(monkeypatch, tmp_path):
    outputs = {"9": {"images": [{"filename": "evil.png", "subfolder": "../../..", "type": "output"}]}}

    async def fake_view(filename, subfolder="", type_="output"):
        raise AssertionError("越界条目不应触发 /view")

    monkeypatch.setattr(ex_mod.runner_client, "fetch_view", fake_view)
    monkeypatch.setattr(ex_mod.config, "get_settings", lambda: _settings(output_dir=str(tmp_path)))
    assert await ex_mod._download_outputs(outputs) == 0


# ---------------------------------------------------------------- 容器日志


@pytest.mark.parametrize("version", VERSIONS)
async def test_container_logs_normalize(monkeypatch, version):
    raw = _fx(version, "logs_raw.json")
    monkeypatch.setattr(rc_mod, "get_settings", lambda: _settings())
    _mock_transport(monkeypatch, {"/internal/logs/raw": lambda req: httpx.Response(200, json=raw)})
    entries = await rc_mod.fetch_container_logs()
    assert [e["level"] for e in entries] == ["info", "info", "info", "error"]
    assert entries[0]["m"] == "got prompt\n"
    assert "50%" in entries[2]["m"]  # \r 进度行已归一
    _mock_transport(monkeypatch, {"/internal/logs/raw": _boom})
    assert await rc_mod.fetch_container_logs() == []  # 失败/缩容 → 空，不炸轮询


# ---------------------------------------------------------------- object_info


@pytest.mark.parametrize("version", VERSIONS)
async def test_object_info_top_level_keys(monkeypatch, version):
    info = _fx(version, "object_info.json")
    monkeypatch.setattr(rc_mod, "get_settings", lambda: _settings())
    _mock_transport(monkeypatch, {"/object_info": lambda req: httpx.Response(200, json=info)})
    got = await rc_mod.fetch_object_info()
    # diff 矩阵 class_types = sorted(info.keys()) —— 顶层必须是 class_type 名平铺
    assert sorted(got.keys()) == ["KSampler", "SaveImage"]
    assert got["KSampler"]["output"] == ["LATENT"]


# ---------------------------------------------------------------- 本地 WS tap


@pytest.mark.parametrize("version", VERSIONS)
async def test_local_ws_tap_status_mapping(monkeypatch, version):
    updates: list[tuple[str, str, str | None]] = []

    async def fake_update(pid, status, error=None):
        updates.append((pid, status, error))

    monkeypatch.setattr(wsm.store, "aupdate_status", fake_update)
    seq = _fx(version, "ws_events_success.json")
    err = _fx(version, "ws_events_error.json")[-1]
    for ev in [seq[1], seq[7], err, seq[0]]:  # start / success / error / status(不映射)
        wsm._tap_event(json.dumps(ev, ensure_ascii=False))
    for _ in range(10):
        await asyncio.sleep(0)
    assert updates == [
        (CLOUD_PID, "running", None),
        (CLOUD_PID, "done", None),
        (CLOUD_PID, "error", json.dumps(err["data"], ensure_ascii=False)[:500]),
    ]


# ---------------------------------------------------------------- 双通道跟踪


@pytest.mark.parametrize("version", VERSIONS)
async def test_track_ws_terminal_fast_path(monkeypatch, version):
    """WS 快路径：history 未入时终态事件先行收敛（中断事件无 exception_message → detail 兜底空）。"""
    monkeypatch.setattr(
        ex_mod.websockets, "connect", lambda *a, **k: _FakeConn(_frames_of(version, "ws_events_interrupted.json")),
    )
    monkeypatch.setattr(ex_mod, "send_event", lambda cid, ev: None)
    monkeypatch.setattr(ex_mod.config, "get_settings", lambda: _settings(task_timeout=30))

    async def fake_history(pid):
        await asyncio.sleep(0.05)  # 给 WS 泵让出调度窗口（history 未入）

    async def fake_logs():
        return []

    monkeypatch.setattr(ex_mod.runner_client, "get_history", fake_history)
    monkeypatch.setattr(ex_mod.runner_client, "fetch_container_logs", fake_logs)
    kind, entry, detail = await ex_mod._track(BRIDGE_PID, {"client_id": "cid", "modal_pid": CLOUD_PID})
    # WS 快路径 kind 走桥接器状态词汇表（execution_interrupted → cancelled，与日志页一致）
    assert (kind, entry, detail) == ("cancelled", None, "")


@pytest.mark.parametrize("version", VERSIONS)
async def test_track_history_authoritative(monkeypatch, version):
    """history 权威路径：无 WS 连接（client_id 空）也以 history 条目收敛并带回条目供输出回传。"""
    body = _fx(version, "history_success.json")
    monkeypatch.setattr(ex_mod.config, "get_settings", lambda: _settings(task_timeout=30))

    async def fake_history(pid):
        return body.get(pid)

    async def fake_logs():
        return []

    monkeypatch.setattr(ex_mod.runner_client, "get_history", fake_history)
    monkeypatch.setattr(ex_mod.runner_client, "fetch_container_logs", fake_logs)
    kind, entry, detail = await ex_mod._track(BRIDGE_PID, {"modal_pid": CLOUD_PID})
    assert kind == "success" and entry is body[CLOUD_PID] and detail == ""

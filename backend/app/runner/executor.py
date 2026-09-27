"""modal 路由请求的后台执行器（M1 Runner 通道）。

prompt_submit 提前应答 200 + 桥接 prompt_id 后，本模块接力完成：
  上传输入文件（modal volume put → 卷 input/，无需等冷启动）
  → 提交云端 ComfyUI /prompt（首个请求等待容器冷启动，startup_timeout 600s）
  → WS 实时转发 progress/executing/status 到浏览器（云端 prompt_id 映射回桥接 id）
  → 完成后按 /history 把输出经 /view 拉回落本地 output_dir（媒体库自动可见）
  → 合成 executed + execution_success/error/interrupted 注入浏览器
  → 请求记录状态机 queued → running → done/error/cancelled。

云端内部 prompt_id 只在本模块流转；凡发往浏览器的 eventId 都换回桥接 prompt_id，
与提交应答一致，前端无感知。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import websockets

from ..core import config
from ..logs import store
from ..modal_volume import client as modal_cli
from . import client as runner_client
from . import console
from ..proxy.ws_relay import send_event

logger = logging.getLogger(__name__)

# ComfyUI WS 终态事件 → 本地状态（其余事件仅转发）
_TERMINAL_STATUS = {
    "execution_success": "done",
    "execution_error": "error",
    "execution_interrupted": "cancelled",
}

# 输入类节点引用的本地文件（LoadImage/LoadImageMask/LoadAudio*/LoadVideo*…）
_MEDIA_INPUT_KEYS = {"image", "audio", "video", "file"}
_MEDIA_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff",
    ".mp4", ".webm", ".mov", ".avi", ".mkv",
    ".mp3", ".wav", ".flac", ".ogg",
}
# history 输出条目里的文件桶（图片/视频/音频节点命名不一）
_OUTPUT_BUCKETS = ("images", "gifs", "video", "audio")

# bridge_prompt_id → 任务态（client_id / modal_pid / task）
_TASKS: dict[str, dict] = {}


def dispatch(prompt_id: str, payload: dict) -> bool:
    """启动云端执行任务（重复提交同一 prompt_id 拒绝）。"""
    if prompt_id in _TASKS:
        return False
    state: dict = {"client_id": payload.get("client_id"), "modal_pid": None, "task": None}
    _TASKS[prompt_id] = state
    state["task"] = asyncio.create_task(_run(prompt_id, payload))
    return True


def interrupt_all() -> None:
    """浏览器 /interrupt 时尽力中断在跑的云端任务（本地 /interrupt 照常透传）。"""
    for state in _TASKS.values():
        task = state.get("task")
        if task and not task.done():
            asyncio.get_running_loop().create_task(runner_client.post_interrupt())


# ------------------------------------------------------------------ 主流程

async def _run(prompt_id: str, payload: dict) -> None:
    state = _TASKS[prompt_id]
    try:
        if runner_client.web_url() is None:
            raise RuntimeError(
                "未配置 Modal web 端点：modal deploy modal-runner/runner.py 后把输出的 URL 填入 "
                "~/.comfybridge/config.toml [modal] web_url"
            )
        console.log(f"[{prompt_id[:8]}] 已受理，路由 Modal 通道执行")
        await _upload_inputs(payload)
        # 提交本身可能等待容器冷启动（最长 600s），客户端读超时已放宽
        t0 = asyncio.get_running_loop().time()
        ack = await runner_client.post_prompt(payload)
        state["modal_pid"] = ack.get("prompt_id")
        if not state["modal_pid"]:
            raise RuntimeError(f"云端提交应答缺少 prompt_id: {str(ack)[:200]}")
        waited = asyncio.get_running_loop().time() - t0
        logger.info("[%s] 云端已受理 modal_pid=%s", prompt_id[:8], state["modal_pid"])
        console.log(
            f"[{prompt_id[:8]}] 云端已受理 modal_pid={state['modal_pid'][:8]}"
            f"（等待 {waited:.1f}s，含容器冷启动）"
        )
        await store.aupdate_status(prompt_id, "running")
        send_event(state["client_id"], {
            "type": "execution_start",
            "data": {"prompt_id": prompt_id, "timestamp": store.now_ms() / 1000.0},
        })
        kind, entry, detail = await _track(prompt_id, state)
        await _finish(prompt_id, state, kind, entry, detail)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 任何环节失败都收敛到 error 终态
        logger.exception("[%s] 云端执行失败", prompt_id[:8])
        await _finish(prompt_id, state, "error", None, str(exc))
    finally:
        state["task"] = None
        # 结束的任务保留 1 小时供查询（modal_pid 映射/日志去重集都不小），随后移除注册表
        # 条目，防止 _TASKS 只进不出导致长跑内存膨胀
        asyncio.get_running_loop().call_later(3600, _TASKS.pop, prompt_id, None)


async def _upload_inputs(payload: dict) -> None:
    """把 prompt 引用的本地输入文件推到数据卷 input/（云端同路径可读）。

    本地没有的输入视为云端卷已有（云端工作流场景），缺失时由云端校验兜底报错。
    """
    names = _collect_input_files(payload.get("prompt") or {})
    if not names:
        return
    pid = str(payload.get("prompt_id") or "")[:8]
    input_root = config.input_dir()
    missing = [n for n in names if not (input_root / n).is_file()]
    if missing:
        console.log(f"[{pid}] 输入 {len(missing)} 个本地缺失，假定已在云端卷（缺失由云端校验兜底）")
    to_upload = [n for n in names if (input_root / n).is_file()]
    if to_upload:
        console.log(f"[{pid}] 上传输入文件 {len(to_upload)} 个 → 数据卷 input/")
    for name in to_upload:
        # 输入名来自提交方工作流：只允许 input/ 内的相对路径，
        # 防 .. / 绝对路径把 input 外的本地文件推上云端卷
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            logger.warning("跳过越界输入引用: %r", name)
            continue
        local = input_root / name
        await modal_cli.run("volume", "put", modal_cli.volume_name(), "-f", str(local), f"input/{name}")
        logger.info("输入已上传数据卷: %s", name)


def _collect_input_files(prompt: dict) -> list[str]:
    names: set[str] = set()
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, val in inputs.items():
            if (key in _MEDIA_INPUT_KEYS and isinstance(val, str) and val
                    and "://" not in val and Path(val).suffix.lower() in _MEDIA_EXTS):
                names.add(val.replace("\\", "/"))
    return sorted(names)


# ------------------------------------------------------------------ 跟踪

async def _track(prompt_id: str, state: dict) -> tuple[str, dict | None, str]:
    """跟踪云端执行直到终态。返回 (kind, history条目|None, detail)。

    双通道：WS 实时转发进度并捕捉终态事件（快）；/history 轮询兜底（权威，
    容器重启/WS 断开也不丢）。终态以先到者为准，kind 冲突时 interrupted 优先。
    """
    modal_pid = state.get("modal_pid") or ""
    terminal: dict = {}  # {"kind", "detail"} —— WS 终态信号
    done = asyncio.Event()

    def on_terminal(etype: str, data: dict) -> None:
        detail = data.get("exception_message") or data.get("exception_type") or ""
        if etype not in _TERMINAL_STATUS:
            return
        terminal.setdefault("kind", _TERMINAL_STATUS[etype])
        terminal["detail"] = detail
        done.set()

    ws_task = None
    if state.get("client_id"):
        ws_task = asyncio.create_task(
            _ws_pump(state["client_id"], modal_pid, prompt_id, on_terminal)
        )
    try:
        deadline = asyncio.get_running_loop().time() + config.get_settings().modal.task_timeout
        while True:
            entry = await runner_client.get_history(modal_pid)
            logs = await runner_client.fetch_container_logs()  # 失败/缩容返回空
            if entry is not None:
                _drain_container_logs(state, logs)  # 收尾前补最后一拍容器日志
                kind, detail = _classify_entry(entry)
                return kind, entry, detail
            _drain_container_logs(state, logs)  # 云端 stdout → 控制台（轮询差分）
            if done.is_set():
                return terminal["kind"], None, terminal.get("detail") or ""
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"云端执行超时（> {config.get_settings().modal.task_timeout}s）")
            await asyncio.sleep(2.5)
    finally:
        if ws_task:
            ws_task.cancel()


def _drain_container_logs(state: dict, entries: list[dict]) -> None:
    """把云端容器 stdout 缓冲差分进控制台（按 (t,m) 去重；单次最多 40 行防洪泛）。"""
    seen = state.setdefault("seen_logs", set())
    new = [e for e in entries if (e["t"], e["m"]) not in seen]
    if len(new) > 40:
        new = new[-40:]
    for e in new:
        seen.add((e["t"], e["m"]))
        console.log(e["m"].rstrip() or " ", e["level"])


def _classify_entry(entry: dict) -> tuple[str, str]:
    """history 条目 → (kind, detail)。messages 含 execution_interrupted 视为中断。"""
    status = entry.get("status") or {}
    detail = ""
    for msg in status.get("messages") or []:
        if isinstance(msg, (list, tuple)) and msg and msg[0] == "execution_error":
            data = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
            detail = data.get("exception_message") or data.get("exception_type") or ""
    for msg in status.get("messages") or []:
        if isinstance(msg, (list, tuple)) and msg and msg[0] == "execution_interrupted":
            return "interrupted", detail
    if status.get("completed") or status.get("status_str") == "success":
        return "success", ""
    return "error", detail or "云端执行失败（无输出）"


async def _ws_pump(client_id: str, modal_pid: str, bridge_pid: str,
                   on_terminal) -> None:
    """云端 WS → 浏览器单向泵：progress/executing/status 实时转发（prompt_id 映射）。"""
    try:
        async with websockets.connect(runner_client.ws_url(client_id), max_size=None) as up:
            last_logged = [0.0]  # 进度节流（同批多步事件只按时间采样写控制台）
            async for raw in up:
                if not isinstance(raw, str):
                    continue  # 二进制预览帧不回传（M1）
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                etype = event.get("type")
                data = event.get("data") or {}
                if data.get("prompt_id") and data["prompt_id"] != modal_pid:
                    continue  # 其他请求的事件不掺和
                if etype == "status":
                    send_event(client_id, event)  # 全局队列状态，无需映射
                    continue
                if etype == "progress":
                    now = asyncio.get_running_loop().time()
                    if now - last_logged[0] >= 0.8:
                        last_logged[0] = now
                        total = data.get("max") or data.get("max_value")
                        console.log(
                            f"[{bridge_pid[:8]}] 节点 {data.get('node')} 进度 "
                            f"{data.get('value')}/{total}"
                        )
                if etype in {"progress", "executing", "execution_start", "execution_cached"}:
                    data["prompt_id"] = bridge_pid
                    send_event(client_id, event)
                elif etype in _TERMINAL_STATUS:
                    data["prompt_id"] = bridge_pid
                    on_terminal(etype, data)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 —— WS 断开不影响轮询兜底
        logger.debug("云端 WS 断开（轮询兜底接管）: %s", exc)


# ------------------------------------------------------------------ 收尾

async def _finish(prompt_id: str, state: dict, kind: str, entry: dict | None, detail: str) -> None:
    client_id = state.get("client_id")
    if kind == "interrupted":
        console.log(f"[{prompt_id[:8]}] 云端执行已中断", "warning")
        await store.aupdate_status(prompt_id, "cancelled")
        send_event(client_id, {"type": "execution_interrupted",
                               "data": {"prompt_id": prompt_id}})
        return
    if kind == "error":
        err = (detail or "云端执行失败")[:500]
        console.log(f"[{prompt_id[:8]}] 执行失败：{err}", "error")
        await store.aupdate_status(prompt_id, "error", err)
        send_event(client_id, {"type": "execution_error",
                               "data": {"prompt_id": prompt_id, "exception_message": err}})
        return

    # success：先把输出拉回本地（UI 收到 executed 事件后会立刻请求 /view，本地必须已就位）
    outputs = (entry or {}).get("outputs") or {}
    try:
        count = await _download_outputs(outputs)
        logger.info("[%s] 输出回传完成：%d 个文件", prompt_id[:8], count)
        console.log(f"[{prompt_id[:8]}] 执行完成，输出回传 {count} 个文件")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] 输出回传失败", prompt_id[:8])
        err = f"输出回传失败: {exc}"[:500]
        console.log(f"[{prompt_id[:8]}] 执行失败：{err}", "error")
        await store.aupdate_status(prompt_id, "error", err)
        send_event(client_id, {"type": "execution_error",
                               "data": {"prompt_id": prompt_id, "exception_message": err}})
        return
    await store.aupdate_status(prompt_id, "done")
    for node_id, node_out in outputs.items():
        send_event(client_id, {"type": "executed", "data": {
            "node": str(node_id), "prompt_id": prompt_id, "output": node_out,
        }})
    send_event(client_id, {"type": "execution_success",
                           "data": {"prompt_id": prompt_id}})


async def _download_outputs(outputs: dict) -> int:
    """把 history 输出逐文件经 /view 拉回本地 output_dir（保持原相对路径）。"""
    out_root = Path(config.get_settings().local.output_dir)
    count = 0
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for bucket in _OUTPUT_BUCKETS:
            for item in node_out.get(bucket) or []:
                filename = item.get("filename")
                if not filename:
                    continue
                subfolder = item.get("subfolder") or ""
                ftype = item.get("type") or "output"
                # subfolder/filename 来自云端 history 响应（不完全可信）：
                # 先校验落点在 output_dir 内（防 .. / 盘符绝对路径越界写盘），再拉取
                dest = (out_root / subfolder / filename).resolve()
                if not dest.is_relative_to(out_root.resolve()):
                    logger.warning("跳过越界输出条目: subfolder=%r filename=%r", subfolder, filename)
                    continue
                data = await runner_client.fetch_view(filename, subfolder, ftype)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                count += 1
    return count

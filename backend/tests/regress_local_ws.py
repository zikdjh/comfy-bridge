# /// script
# requires-python = ">=3.10"
# ///
"""回归：本地路由 + WS 中继。开一个 WS 客户端（clientId=bridge-ws-regress），
提交全本地模型图 → 应路由 local 执行，且 WS 能收到该 prompt 的事件。"""
import asyncio
import json
import time
import urllib.request

import websockets

BASE = "http://127.0.0.1:8189"
CID = "bridge-ws-regress"

PROMPT = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_nvfp4.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "ink wash painting, distant peaks"}},
    "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
    "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "8": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": 314159, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
    "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "bridge_local_regress"}},
}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


async def main():
    events: list[str] = []
    async with websockets.connect(f"ws://127.0.0.1:8189/ws?clientId={CID}", max_size=None) as ws:
        req = urllib.request.Request(BASE + "/prompt", data=json.dumps(
            {"prompt_id": "", "client_id": CID, "prompt": PROMPT}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: ASYNC210 —— 手动回归脚本，顺序提交无需异步 HTTP
            ack = json.loads(resp.read())
        pid = ack["prompt_id"]
        print("submit:", resp.status, pid)

        deadline = time.monotonic() + 180
        done_flag = False
        while time.monotonic() < deadline and not done_flag:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except TimeoutError:
                rec = None
                recs = get("/api/v1/requests?limit=3")["items"]
                rec = next((r for r in recs if r["prompt_id"] == pid), {})
                done_flag = rec.get("status") in {"done", "error", "cancelled"}
                continue
            if isinstance(raw, bytes):
                continue
            ev = json.loads(raw)
            data = ev.get("data") or {}
            if data.get("prompt_id") == pid:
                if ev["type"] not in events:
                    events.append(ev["type"])
                    print("event:", ev["type"])
                if ev["type"] in {"execution_success", "execution_error", "execution_interrupted"}:
                    done_flag = True

    recs = get("/api/v1/requests?limit=3")["items"]
    rec = next((r for r in recs if r["prompt_id"] == pid), {})
    print("record:", json.dumps({k: rec.get(k) for k in ("id", "status", "backend", "final_backend", "duration_ms")}, ensure_ascii=False))
    print("ws events seen:", events)
    assert rec.get("final_backend") == "local", "本地请求被误路由"
    assert "execution_success" in events, "WS 未收到执行事件"
    print("REGRESS-OK")


asyncio.run(main())

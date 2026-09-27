# /// script
# requires-python = ">=3.10"
# ///
"""E2E：云端专有模型（krea2 fp8_scaled，本地无副本）经 :8189 提交 → modal 执行 → 输出回传。

提交即返回 200 + 桥接 prompt_id；执行器后台接力（冷启动/提交/回传）。
轮询桥接器请求记录直到终态，随后校验媒体库与本地落盘文件。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8189"

PROMPT = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
    "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "krea2_darkbrush.safetensors", "strength_model": 1.0, "model": ["1", 0]}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "monochrome ink wash style, a lone crane flying over misty mountains, moonlit river"}},
    "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
    "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": 990726, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
    "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "bridge_modal_e2e"}},
}


def post(path: str, payload: dict):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read())


status, ack = post("/prompt", {"prompt_id": "", "client_id": "bridge-e2e-m1", "prompt": PROMPT})
print("submit:", status, json.dumps(ack, ensure_ascii=False))
if status != 200:
    sys.exit(1)

pid = ack["prompt_id"]
print("bridge prompt_id:", pid)

last = None
deadline = time.time() + 540
while time.time() < deadline:
    recs = get("/api/v1/requests?limit=3")["items"]
    rec = next((r for r in recs if r["prompt_id"] == pid), None)
    cur = (rec or {}).get("status")
    if cur != last:
        print(f"[{time.strftime('%H:%M:%S')}] status: {last} -> {cur}", (rec or {}).get("error") or "")
        last = cur
    if cur in {"done", "error", "cancelled", "blocked"}:
        break
    time.sleep(10)

recs = get("/api/v1/requests?limit=3")["items"]
rec = next((r for r in recs if r["prompt_id"] == pid), {})
print("final record:", json.dumps({k: rec.get(k) for k in ("id", "status", "backend", "final_backend", "error", "duration_ms")}, ensure_ascii=False))

media = get("/api/v1/media/local?kind=all&limit=5")
print("media latest:", json.dumps(media["items"][:3], ensure_ascii=False))

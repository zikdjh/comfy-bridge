# /// script
# requires-python = ">=3.10"
# ///
"""WS 探针：直连云端 /ws，把收到的文本事件打印到 stdout（验证事件到达执行容器对应的 socket）。"""
import asyncio
import sys
import time

import websockets

sys.path.insert(0, ".")
from app.runner import client as runner_client  # noqa: E402


async def main():
    url = runner_client.ws_url("bridge-probe")
    print("connecting:", url, flush=True)
    async with websockets.connect(url, max_size=None, open_timeout=600) as ws:
        print(f"[{time.strftime('%H:%M:%S')}] WS connected", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=290)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, bytes):
                continue
            t = time.strftime("%H:%M:%S")
            print(f"{t} {raw[:220]}", flush=True)


asyncio.run(main())

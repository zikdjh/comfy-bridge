"""comfy-bridge 命令行入口（pipx / pip 安装后的启动脚本）。

仓库开发不走这里，直接 uv run uvicorn app.main:app --port 8189。
安装后：comfy-bridge [--host H] [--port P]，控制台在 http://H:P/bridge/，
ComfyUI 数据面仍在根路径（出图地址不变）。
"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="comfy-bridge",
        description="ComfyBridge：本地 ComfyUI × Modal 云端 GPU 桥接器",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8189, help="监听端口（默认 8189）")
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

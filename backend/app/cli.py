"""comfy-bridge 命令行入口（pipx / pip 安装后的启动脚本）。

仓库开发不走这里，直接 uv run uvicorn app.main:app --port 18189。
安装后：comfy-bridge [--host H] [--port P]，控制台在 http://H:P/bridge/，
ComfyUI 数据面仍在根路径（出图地址不变）。
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from .core.management_auth import is_loopback_host, management_token

DEFAULT_PORT = 18189


def _validate_external_listen(host: str) -> None:
    """非回环监听必须配置管理令牌，避免管理 API/GUI 裸奔到局域网或公网。"""
    if is_loopback_host(host):
        return
    if management_token():
        return
    print(
        "拒绝启动：--host 不是回环地址，但未配置管理令牌。\n"
        "请设置 CB_MANAGEMENT_TOKEN，或写入 <配置目录>/management_token 后重试；\n"
        "若只需本机使用，请保持默认 --host 127.0.0.1。",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="comfy-bridge",
        description="ComfyBridge：本地 ComfyUI × Modal 云端 GPU 桥接器",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CB_BRIDGE_PORT", DEFAULT_PORT)),
        help="监听端口（默认 18189，可用 CB_BRIDGE_PORT 覆盖）",
    )
    args = parser.parse_args()
    _validate_external_listen(args.host)
    # health/管理台以实际启动参数为准；config.toml 的 bridge.listen 是持久配置，
    # 不应覆盖一次性的 --port/--host（否则“前往出图界面”会跳到旧端口）。
    os.environ["CB_BRIDGE_RUNTIME_LISTEN"] = f"{args.host}:{args.port}"
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

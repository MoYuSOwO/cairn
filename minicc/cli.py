"""
MiniCC 命令行入口。

用法:
  minicc             启动 TUI（内嵌模式，单进程）
  minicc serve       启动后端服务（FastAPI + WebSocket）
  minicc tui         启动 TUI 客户端（连接后端服务）
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicc", description="MiniCC - 极简 AI 编程助手")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="启动后端服务").add_argument(
        "--port", type=int, default=8720, help="服务端口 (默认 8720)"
    )
    sub.add_parser("serve").add_argument(
        "--host", type=str, default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)"
    )

    tui_p = sub.add_parser("tui", help="启动 TUI 客户端")
    tui_p.add_argument("--url", type=str, default="ws://127.0.0.1:8720/ws", help="后端 WebSocket 地址")

    args = parser.parse_args()

    if args.command == "serve":
        from minicc.server import run_server

        run_server(host=args.host, port=args.port)
    elif args.command == "tui":
        from minicc.tui.app import run_client

        run_client(server_url=args.url)
    else:
        from minicc.tui.app import run_embedded

        run_embedded()


if __name__ == "__main__":
    main()

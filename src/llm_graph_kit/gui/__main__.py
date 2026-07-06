"""`python -m llm_graph_kit.gui` でサーバーを起動する。"""
from __future__ import annotations

import argparse

from .server import launch


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llm_graph_kit.gui",
        description="Launch the llm-graph-kit no-code GUI in your browser.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="enable auto-reload (dev)")
    args = parser.parse_args()

    print(f"\n  Open http://{args.host}:{args.port}/ in your browser.\n")
    launch(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

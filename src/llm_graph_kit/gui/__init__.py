"""GUI for llm-graph-kit.

ブラウザ上でノードを繋いでエージェントを組み立てる no-code エディタ。
FastAPI バックエンド + Drawflow.js ベースのフロントエンド。

Usage:
    python -m llm_graph_kit.gui
    # または
    from llm_graph_kit.gui import launch
    launch(host="127.0.0.1", port=8000)
"""
from .server import launch, create_app

__all__ = ["launch", "create_app"]

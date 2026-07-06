from .llm_graph import LLMGraph, NodeState, NodeFunc

__all__ = [
    "LLMGraph",
    "NodeState",
    "NodeFunc",
]


def launch_gui(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """ブラウザベースのノードエディタ GUI を起動する。

    `llm-graph-kit[gui]` extras (fastapi, uvicorn) が必要。
    """
    from .gui.server import launch
    launch(host=host, port=port, reload=reload)

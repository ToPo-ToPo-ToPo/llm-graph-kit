"""FastAPI サーバー: GUI の配信、グラフ実行、コード生成 API を提供する。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json
import traceback

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "FastAPI is required for the GUI. Install with `uv add 'llm-graph-kit[gui]'` "
        "or `pip install fastapi uvicorn`."
    ) from e

from ..llm_graph import LLMGraph
from .builder import build_graph_from_spec, parse_initial_state
from .codegen import generate_python_code


_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="llm-graph-kit GUI", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(str(_STATIC_DIR / "index.html"))

    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    @app.post("/api/mermaid")
    def api_mermaid(payload: Dict[str, Any]) -> JSONResponse:
        try:
            graph = build_graph_from_spec(payload)
            return JSONResponse({"mermaid": graph.get_graph_mermaid()})
        except Exception as e:
            return JSONResponse(
                {"error": str(e), "traceback": traceback.format_exc()},
                status_code=400,
            )

    @app.post("/api/codegen")
    def api_codegen(payload: Dict[str, Any]) -> JSONResponse:
        try:
            code = generate_python_code(payload)
            return JSONResponse({"code": code})
        except Exception as e:
            return JSONResponse(
                {"error": str(e), "traceback": traceback.format_exc()},
                status_code=400,
            )

    @app.post("/api/run")
    def api_run(payload: Dict[str, Any]) -> StreamingResponse:
        """グラフを実行して、yield されるイベントを SSE で流す。"""

        def event_stream():
            try:
                graph = build_graph_from_spec(payload)
                initial_state = parse_initial_state(payload)
                max_steps = int(payload.get("max_steps") or 100)

                # 開始イベント
                yield _sse({"type": "log", "node": "system", "content": "run started"})

                gen = graph.run(initial_state, max_steps=max_steps)
                final_state: Any = None
                try:
                    while True:
                        event = next(gen)
                        yield _sse(_make_safe(event))
                except StopIteration as e:
                    final_state = e.value

                yield _sse({
                    "type": "final",
                    "state": _make_safe(final_state),
                })
                yield _sse({"type": "done"})
            except Exception as e:
                yield _sse({
                    "type": "fatal",
                    "content": str(e),
                    "traceback": traceback.format_exc(),
                })
                yield _sse({"type": "done"})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _sse(data: Any) -> str:
    """SSE フォーマットでメッセージを整形する。"""
    try:
        s = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        s = json.dumps({"type": "error", "content": str(data)}, ensure_ascii=False)
    return f"data: {s}\n\n"


def _make_safe(value: Any) -> Any:
    """JSON シリアライズできない型を文字列に落とす。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _make_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_safe(v) for v in value]
    return repr(value)


def launch(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """GUI サーバーを起動する。

    Args:
        host: バインドアドレス。デフォルトは 127.0.0.1（外部からはアクセス不可）。
            意図的に LAN 公開する場合のみ 0.0.0.0 にする。GUI はユーザー入力の
            Python コードを実行するため、公開ネットワークでの利用は厳禁。
        port: ポート番号。
        reload: 開発用オートリロード。
    """
    try:
        import uvicorn
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "uvicorn is required for launch(). Install with "
            "`uv add 'llm-graph-kit[gui]'`."
        ) from e

    if reload:
        # reload には import string が必要
        uvicorn.run(
            "llm_graph_kit.gui.server:create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        uvicorn.run(create_app(), host=host, port=port)

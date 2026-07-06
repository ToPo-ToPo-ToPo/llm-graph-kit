"""
LLM を使ったエージェントの動作デモ（自然言語をストリーミング表示する）。

質問に答えるノードと、答えをチェックするノードからなる 2 段グラフ。
チェックで「短すぎる」と判定されたらやり直し、最大 3 回でループを抜ける。
ノードが LLM のトークンを yield し、それが run() の出力にそのまま流れて
自然言語が逐次表示される様子を確認できる。

このリポジトリは特定の LLM ライブラリに依存しない（augllm 等は不要）。
そのため本デモは、追加インストールなしで動くように**モック LLM を内蔵**して
いる。実際の LLM を使いたい場合は、`respond(system_prompt, user_text, stream)`
で文字列チャンクを yield するクライアントを用意し、QAAgent に注入するだけ。

実行方法:
    python tests/demo_llm_agent.py        # モック LLM で自然言語の流れを表示
"""
import os
import sys
import time
from typing import TypedDict

# tests/ から実行してもリポジトリ直下の `src` パッケージを解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings

from src.llm_graph_kit import LLMGraph, NodeState

warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r"Creating LLMGraph without state_schema is deprecated",
)


# ---------------------------------------------------------------------------
# モック LLM（追加依存なしでデモを動かすための最小実装）
# ---------------------------------------------------------------------------
class MockLLM:
    """自然言語をストリーミングで返すダミー LLM。

    実 LLM の `respond(system_prompt, user_text, stream=True)` と同じ
    インターフェースを持ち、文字列チャンクを 1 つずつ yield する。
    呼び出し回数に応じて回答を長くし、リトライループが発生する様子を再現する。
    """

    # 1 回目はわざと短く（チェックで「短すぎる」と判定させる）、
    # 2 回目で十分な長さの回答を返す。
    _ANSWERS = [
        "約6400kmです。",
        "地球の半径はおよそ6371kmです。"
        "赤道方向がやや長い回転楕円体のため、赤道半径は約6378km、"
        "極半径は約6357kmと、測る向きによって少し値が変わります。",
    ]

    def __init__(self, delay: float = 0.02) -> None:
        self._calls = 0
        self._delay = delay  # 表示が流れて見えるようにわずかに待つ

    def respond(self, system_prompt: str, user_text: str, stream: bool = True):
        answer = self._ANSWERS[min(self._calls, len(self._ANSWERS) - 1)]
        self._calls += 1
        # 文字単位でチャンクに割って、LLM のトークンストリームを模す
        for ch in answer:
            if self._delay:
                time.sleep(self._delay)
            yield ch


# ---------------------------------------------------------------------------
# ステートのスキーマ
# ---------------------------------------------------------------------------
class QAState(TypedDict, total=False):
    question: str
    answer: str
    attempts: int
    decision: str


# ---------------------------------------------------------------------------
# エージェントクラス（LLM は依存注入で受け取る）
# ---------------------------------------------------------------------------
class QAAgent:
    """質問応答エージェント。LLM を保持し、グラフの定義と実行を提供する。"""

    def __init__(self, llm) -> None:
        self.llm = llm

    # --- 公開エントリポイント ---
    def run(self, question: str):
        """質問を受け取り、グラフ実行中のイベントを呼び出し側へ yield する。"""
        graph = self.build_graph()

        # まずグラフ構造を log イベントとして流す（呼び出し側で可視化できる）
        yield {
            "type": "log",
            "node": "system",
            "content": f"Workflow Definition:\n{graph.get_graph_mermaid()}",
        }

        initial_state = {"question": question, "attempts": 0}
        yield from graph.run(initial_state)

    # --- グラフ定義 ---
    def build_graph(self) -> LLMGraph:
        g = LLMGraph(state_schema=QAState)

        g.add_node(name="answer", func=self._answer)
        g.add_node(name="check", func=self._check)

        g.add_edge(LLMGraph.START, "answer")
        g.add_edge("answer", "check")
        g.add_conditional_edge(
            "check",
            "decision",
            {
                "retry": "answer",
                "ok": LLMGraph.END,
            },
        )
        return g

    # --- ノード: LLM で回答を生成（ストリーミング）---
    def _answer(self, state: NodeState):
        node_name = "answer"
        yield {"type": "log", "node": node_name, "content": "answering..."}

        system_prompt = "あなたは簡潔に答えるアシスタントです。"
        user_prompt = f"質問: {state['question']}"

        text = ""
        for chunk in self.llm.respond(
            system_prompt=system_prompt, user_text=user_prompt, stream=True
        ):
            text += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "content": chunk,
            }

        return {"answer": text, "attempts": state.get("attempts", 0) + 1}

    # --- ノード: 回答の品質チェックと分岐シグナルの決定 ---
    def _check(self, state: NodeState):
        node_name = "check"

        too_short = len(state["answer"]) < 30
        give_up = state["attempts"] >= 3
        decision = "retry" if (too_short and not give_up) else "ok"

        yield {
            "type": "log",
            "node": node_name,
            "content": (
                f"len={len(state['answer'])} attempts={state['attempts']}"
                f" -> {decision}"
            ),
        }
        return {"decision": decision}


# ---------------------------------------------------------------------------
# メインプログラム
# ---------------------------------------------------------------------------
def main() -> int:
    # 1. エージェントを構築（モック LLM を注入）
    #    実 LLM を使う場合はここを差し替える。例:
    #        from your_llm_lib import YourLLM
    #        llm = YourLLM(model_path="...")
    llm = MockLLM()
    agent = QAAgent(llm=llm)

    # 2. 入力
    question = "地球の半径は何 km?"
    print(f"Request: {question}\n")

    # 3. 実行（ストリーミングを受け取り、イベント種別ごとに表示先を変える）
    for event in agent.run(question=question):
        # LLM 出力のチャンク（自然言語をそのまま逐次表示）
        if event["type"] == "answer_text":
            print(event["content"], end="", flush=True)

        # ログ
        elif event["type"] == "log":
            print(f"\n[LOG] {event['node']}: {event['content']}")

        # ノード内例外（ライブラリが自動で yield する）
        elif event["type"] == "error":
            print(f"\n[ERROR] {event['agent']}: {event['content']}")

    print("\n\nProcess Completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

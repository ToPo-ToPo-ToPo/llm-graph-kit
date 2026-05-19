"""
LLM を使った最小サンプル(エージェントの理想構成例)。

質問に答えるノードと、答えをチェックするノードからなる 2 段グラフ。
チェックで「短すぎる」と判定されたらやり直す。最大 3 回でループを抜ける。

構成のポイント:
  - LLM とグラフはエージェントクラスに閉じ込める
  - グラフは build_graph() に分離(再利用・テストしやすい)
  - 各ノードはエージェントの private メソッドとして実装
  - 公開エントリポイント run() は initial_state の組み立てと
    Mermaid 図のログ送出 + graph.run() の yield from を担当

実行:
    python example_with_llm.py
"""
from typing import TypedDict

from augllm import MlxLLM

from llm_graph_kit import LLMGraph, NodeState


# ---------------------------------------------------------------------------
# ステートのスキーマ
# ---------------------------------------------------------------------------
class QAState(TypedDict, total=False):
    question: str
    answer: str
    attempts: int
    decision: str


# ---------------------------------------------------------------------------
# エージェントクラス
# ---------------------------------------------------------------------------
class QAAgent:
    """質問応答エージェント。LLM を保持し、グラフの定義と実行を提供する。"""

    # --------------------------------------------------
    # 初期化
    # --------------------------------------------------
    def __init__(self, model_path: str) -> None:
        print(f"Loading Model: {model_path} ...")
        self.llm = MlxLLM(model_path=model_path)

    # --------------------------------------------------
    # 公開エントリポイント
    # --------------------------------------------------
    def run(self, question: str):
        """質問を受け取り、グラフ実行中のイベントを呼び出し側へ yield する。"""
        graph = self.build_graph()

        # まずグラフ構造を log イベントとして流しておく(呼び出し側で可視化できる)
        yield {
            "type": "log",
            "node": "system",
            "content": f"Workflow Definition:\n{graph.get_graph_mermaid()}",
        }

        # 下位グラフのイベントをそのまま中継
        initial_state = {"question": question, "attempts": 0}
        yield from graph.run(initial_state)

    # --------------------------------------------------
    # グラフ定義
    # --------------------------------------------------
    def build_graph(self) -> LLMGraph:
        """このエージェントのワークフローを構築して返す。"""
        g = LLMGraph(state_schema=QAState)

        # ノード登録
        g.add_node(name="answer", func=self._answer)
        g.add_node(name="check", func=self._check)

        # エッジ定義
        g.add_edge(LLMGraph.START, "answer")
        g.add_edge("answer", "check")

        # 条件付きエッジ: state["decision"] によって遷移先を切り替える
        g.add_conditional_edge(
            "check",
            "decision",
            {
                "retry": "answer",
                "ok": LLMGraph.END,
            },
        )
        return g

    # --------------------------------------------------
    # ノード: LLM で回答を生成(ストリーミング)
    # --------------------------------------------------
    def _answer(self, state: NodeState):
        node_name = "answer"
        yield {"type": "log", "node": node_name, "content": "answering..."}

        system_prompt = "あなたは簡潔に答えるアシスタントです。"
        user_prompt = f"質問: {state['question']}"

        # LLM のチャンクをそのまま呼び出し側へ流す
        text = ""
        for chunk in self.llm.respond(
            system_prompt=system_prompt, user_text=user_prompt, stream=True
        ):
            text += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "taskId": f"{node_name}-answer-text",
                "content": chunk,
            }

        # state を更新(回答本文と試行回数のインクリメント)
        return {"answer": text, "attempts": state.get("attempts", 0) + 1}

    # --------------------------------------------------
    # ノード: 回答の品質チェックと分岐シグナルの決定
    # --------------------------------------------------
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
if __name__ == "__main__":
    # 1. エージェントを構築
    LLM_PATH = "mlx-community/Qwen3.6-27B-4bit"
    agent = QAAgent(model_path=LLM_PATH)

    # 2. 入力
    question = "地球の半径は何 km?"
    print(f"Request: {question}\n")

    # 3. 実行(ストリーミングを受け取り、イベント種別ごとに表示先を変える)
    for event in agent.run(question=question):

        # LLM 出力のチャンク
        if event["type"] == "answer_text":
            print(event["content"], end="", flush=True)

        # ログ
        elif event["type"] == "log":
            print(f"\n[LOG] {event['node']}: {event['content']}")

        # ノード内例外(ライブラリが自動で yield する)
        elif event["type"] == "error":
            print(f"\n[ERROR] {event['agent']}: {event['content']}")

    print("\n\nProcess Completed.")

"""
LLM を使った最小サンプル。

質問に答えるノードと、答えをチェックするノードからなる 2 段グラフ。
チェックで「短すぎる」と判定されたらやり直す。最大 3 回でループを抜ける。

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
# グラフ構築（LLM を受け取り、ノード関数にクロージャで埋め込む）
# ---------------------------------------------------------------------------
def build_qa_graph(llm: MlxLLM) -> LLMGraph:

    def answer_node(state: NodeState):
        # LLM のチャンクをそのまま呼び出し側へストリーミング
        prompt = f"質問: {state['question']}\n簡潔に答えてください。"
        text = ""
        for chunk in llm.respond(system_prompt="", user_text=prompt, stream=True):
            text += chunk
            yield {"type": "answer_text", "content": chunk}
        return {"answer": text, "attempts": state.get("attempts", 0) + 1}

    def check_node(state: NodeState):
        too_short = len(state["answer"]) < 30
        give_up = state["attempts"] >= 3
        decision = "retry" if (too_short and not give_up) else "ok"
        yield {"type": "log",
               "content": f"len={len(state['answer'])} attempts={state['attempts']} -> {decision}"}
        return {"decision": decision}

    g = LLMGraph(state_schema=QAState)
    g.add_node("answer", answer_node)
    g.add_node("check", check_node)
    g.add_edge(LLMGraph.START, "answer")
    g.add_edge("answer", "check")
    g.add_conditional_edge(
        "check", "decision",
        {"retry": "answer", "ok": LLMGraph.END},
    )
    return g


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    llm = MlxLLM(model_path="mlx-community/Qwen3.6-27B-4bit")
    graph = build_qa_graph(llm)
    print(graph.get_graph_mermaid())

    for event in graph.run({"question": "地球の半径は何 km?", "attempts": 0}):
        if event["type"] == "answer_text":
            print(event["content"], end="", flush=True)
        elif event["type"] == "log":
            print(f"\n[LOG] {event['content']}")
        elif event["type"] == "error":
            print(f"\n[ERROR] {event['content']}")
    print()

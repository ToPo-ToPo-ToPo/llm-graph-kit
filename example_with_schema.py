"""
state_schema を使ったサンプル（LLM 非依存）。

TypedDict でステートのキーを宣言することで:
  - 実行時に未宣言キーへの書き込みを検出して即時に ValueError を発生
  - mypy / IDE で補完と型チェックが効く

実行:
    python example_with_schema.py
"""
from typing import TypedDict, Optional, List

from src.llm_graph_kit import LLMGraph, NodeState


# ---------------------------------------------------------------------------
# 1. ステートのスキーマを TypedDict で宣言
# ---------------------------------------------------------------------------
class CounterState(TypedDict, total=False):
    """カウンタグラフのステート定義。total=False で全フィールドを任意扱いに。"""
    input: str
    count: int
    history: List[str]
    decision: str            # 条件分岐で参照するキー
    final_message: Optional[str]


# ---------------------------------------------------------------------------
# 2. ノード関数
# ---------------------------------------------------------------------------
def start_node(state: NodeState):
    return {
        "count": 0,
        "history": [f"started with input={state['input']}"],
    }


def increment_node(state: NodeState):
    new_count = state["count"] + 1
    history = state["history"] + [f"tick {new_count}"]
    decision = "stop" if new_count >= 3 else "continue"
    return {
        "count": new_count,
        "history": history,
        "decision": decision,
    }


def finish_node(state: NodeState):
    return {
        "history": state["history"] + ["done"],
        "final_message": f"completed after {state['count']} ticks",
    }


# ---------------------------------------------------------------------------
# 3. グラフ構築
# ---------------------------------------------------------------------------
def build_graph() -> LLMGraph:
    # state_schema を渡すと、未宣言キーは即座に ValueError になる
    g = LLMGraph(state_schema=CounterState)

    g.add_node("start", start_node)
    g.add_node("increment", increment_node)
    g.add_node("finish", finish_node)

    g.add_edge(LLMGraph.START, "start")
    g.add_edge("start", "increment")
    g.add_conditional_edge(
        "increment",
        "decision",
        {"continue": "increment", "stop": "finish"},
    )
    g.add_edge("finish", LLMGraph.END)
    return g


# ---------------------------------------------------------------------------
# 4. 実行
# ---------------------------------------------------------------------------
def main():
    g = build_graph()

    # run() はジェネレータを返す。最終 state は StopIteration.value で受け取る
    gen = g.run({"input": "hello"})
    final_state = None
    try:
        while True:
            event = next(gen)
            print("event:", event)
    except StopIteration as e:
        final_state = e.value

    print("\n--- final state ---")
    for k, v in final_state.items():
        print(f"  {k}: {v}")

    # -----------------------------------------------------------------------
    # 違反例: 未宣言キーや予約キーは即座に ValueError
    # -----------------------------------------------------------------------
    print("\n--- schema violations ---")

    try:
        list(g.run({"input": "x", "typo_key": 1}))
    except ValueError as e:
        print("[unknown initial key]", e)

    try:
        list(g.run({"__errors__": ["pre"]}))
    except ValueError as e:
        print("[reserved initial key]", e)

    # 条件分岐の condition が未宣言キーの場合も構築時に弾かれる
    try:
        bad = LLMGraph(state_schema=CounterState)
        bad.add_node("a", lambda s: {"decision": "stop"})
        bad.add_edge(LLMGraph.START, "a")
        bad.add_conditional_edge("a", "dcision", {"stop": LLMGraph.END})  # typo
    except ValueError as e:
        print("[unknown condition key]", e)


if __name__ == "__main__":
    main()

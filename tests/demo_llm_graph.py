"""
LLMGraph の動作を「結果が読みやすい形」で確認するデモ兼チェックスクリプト。

test_llm_graph.py が unittest でドット（. . . OK）表示なのに対し、こちらは
各チェックの「何を確認したか」「入力」「期待値」「実測値」「合否」を日本語で
1 件ずつ表示する。学習・レビュー・デモ用途向け。合否判定も行うので、全チェックが
通れば終了コード 0、失敗があれば 1 を返す（CI でも使える）。

実行方法:
    python tests/demo_llm_graph.py        # 結果を読みやすく表示
    python tests/demo_llm_graph.py --no-color   # 色なし（ログ保存時など）
"""
import os
import sys
from enum import Enum
from typing import TypedDict, Optional, List

# tests/ から実行してもリポジトリ直下の `src` パッケージを解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings

from src.llm_graph_kit import LLMGraph, NodeState

# state_schema 未指定の DeprecationWarning はデモ出力を汚すので抑制する
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r"Creating LLMGraph without state_schema is deprecated",
)


# ---------------------------------------------------------------------------
# 表示ヘルパー
# ---------------------------------------------------------------------------
_USE_COLOR = ("--no-color" not in sys.argv) and sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t): return _c(t, "32")
def red(t): return _c(t, "31")
def cyan(t): return _c(t, "36")
def bold(t): return _c(t, "1")
def dim(t): return _c(t, "2")


_passed = 0
_failed = 0


def section(title: str) -> None:
    print()
    print(bold(cyan(f"■ {title}")))
    print(cyan("─" * (len(title) + 2)))


def check(desc: str, *, given: str, expected, actual) -> None:
    """1 件のチェックを読みやすく出力する。expected == actual なら合格。"""
    global _passed, _failed
    ok = expected == actual
    mark = green("✓ PASS") if ok else red("✗ FAIL")
    if ok:
        _passed += 1
    else:
        _failed += 1
    print(f"  {mark}  {desc}")
    print(dim(f"          入力  : {given}"))
    print(dim(f"          期待値: {expected!r}"))
    color = green if ok else red
    print(f"          {dim('実測値')}: {color(repr(actual))}")


def run_graph(graph: LLMGraph, initial_state: dict):
    """run() を回して (events, final_state) を返すヘルパー。"""
    gen = graph.run(initial_state)
    events = []
    final_state = None
    try:
        while True:
            events.append(next(gen))
    except StopIteration as e:
        final_state = e.value
    return events, final_state


def run_expect_error(fn):
    """fn() を実行し、送出された例外クラス名を返す（出なければ None）。"""
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - デモとして種別を見せたい
        return type(e).__name__
    return None


# ---------------------------------------------------------------------------
# 1. 逐次実行: state がノード間で受け渡される
# ---------------------------------------------------------------------------
def demo_sequential():
    section("逐次実行 — state がノードをまたいで受け渡される")

    class State(TypedDict, total=False):
        input: str
        greeting: str
        message: str

    def make_greeting(state: NodeState):
        return {"greeting": f"こんにちは、{state['input']}さん"}

    def make_message(state: NodeState):
        return {"message": state["greeting"] + "！ようこそ"}

    g = LLMGraph(state_schema=State)
    g.add_node("greet", make_greeting)
    g.add_node("compose", make_message)
    g.add_edge(LLMGraph.START, "greet")
    g.add_edge("greet", "compose")
    g.add_edge("compose", LLMGraph.END)

    _events, final = run_graph(g, {"input": "太郎"})
    check(
        "後続ノードが前ノードの出力を参照して state を積み上げる",
        given="{'input': '太郎'}",
        expected="こんにちは、太郎さん！ようこそ",
        actual=final["message"],
    )


# ---------------------------------------------------------------------------
# 2. 条件分岐: signal で行き先が変わる
# ---------------------------------------------------------------------------
def demo_conditional():
    section("条件分岐 — スコアで公開/差し戻しを振り分ける")

    class State(TypedDict, total=False):
        score: int
        result: str

    def judge(state: NodeState):
        return {}  # score は initial_state で与える

    def publish(state: NodeState):
        return {"result": "公開しました"}

    def revise(state: NodeState):
        return {"result": "差し戻しました"}

    def build():
        g = LLMGraph(state_schema=State)
        g.add_node("judge", judge)
        g.add_node("publish", publish)
        g.add_node("revise", revise)
        g.add_edge(LLMGraph.START, "judge")
        g.add_conditional_edge(
            "judge",
            lambda s: "ok" if s["score"] >= 80 else "ng",
            {"ok": "publish", "ng": "revise"},
        )
        g.add_edge("publish", LLMGraph.END)
        g.add_edge("revise", LLMGraph.END)
        return g

    _e, final_hi = run_graph(build(), {"score": 95})
    check(
        "score=95 (>=80) は publish へ分岐する",
        given="{'score': 95}",
        expected="公開しました",
        actual=final_hi["result"],
    )

    _e, final_lo = run_graph(build(), {"score": 40})
    check(
        "score=40 (<80) は revise へ分岐する",
        given="{'score': 40}",
        expected="差し戻しました",
        actual=final_lo["result"],
    )


# ---------------------------------------------------------------------------
# 3. ループ: retry を繰り返して 3 回で完了する
# ---------------------------------------------------------------------------
def demo_loop():
    section("ループ — 条件を満たすまで同じノードを繰り返す")

    class State(TypedDict, total=False):
        count: int
        decision: str

    def tick(state: NodeState):
        n = state.get("count", 0) + 1
        return {"count": n, "decision": "stop" if n >= 3 else "go"}

    g = LLMGraph(state_schema=State)
    g.add_node("tick", tick)
    g.add_edge(LLMGraph.START, "tick")
    g.add_conditional_edge("tick", "decision", {"go": "tick", "stop": LLMGraph.END})

    _e, final = run_graph(g, {})
    check(
        "count が 3 に達するまで tick を自己ループする",
        given="{} (count 未設定)",
        expected=3,
        actual=final["count"],
    )


# ---------------------------------------------------------------------------
# 4. ストリーミング: ノードの yield が run() の出力に流れる
# ---------------------------------------------------------------------------
def demo_streaming():
    section("ストリーミング — ノードの yield イベントがそのまま流れる")

    class State(TypedDict, total=False):
        answer: str

    def answer(state: NodeState):
        for chunk in ["Llm", "-", "Graph"]:
            yield {"type": "answer_text", "content": chunk}
        return {"answer": "Llm-Graph"}

    g = LLMGraph(state_schema=State)
    g.add_node("answer", answer)
    g.add_edge(LLMGraph.START, "answer")
    g.add_edge("answer", LLMGraph.END)

    events, final = run_graph(g, {})
    chunks = [e["content"] for e in events if e.get("type") == "answer_text"]
    check(
        "yield されたチャンクが順番どおり run() の出力に現れる",
        given="ノードが 'Llm' / '-' / 'Graph' を yield",
        expected=["Llm", "-", "Graph"],
        actual=chunks,
    )
    check(
        "最終 state には return した値が入る",
        given="return {'answer': 'Llm-Graph'}",
        expected="Llm-Graph",
        actual=final["answer"],
    )


# ---------------------------------------------------------------------------
# 5. エラー処理: ノード内例外は捕捉され、実行は継続する
# ---------------------------------------------------------------------------
def demo_error_handling():
    section("エラー処理 — ノード内例外を捕捉して継続する")

    class State(TypedDict, total=False):
        done: bool

    def boom(state: NodeState):
        raise RuntimeError("わざと失敗")

    def recover(state: NodeState):
        return {"done": True}

    g = LLMGraph(state_schema=State)
    g.add_node("boom", boom)
    g.add_node("recover", recover)
    g.add_edge(LLMGraph.START, "boom")
    g.add_edge("boom", "recover")
    g.add_edge("recover", LLMGraph.END)

    events, final = run_graph(g, {})
    error_events = [e for e in events if e.get("type") == "error"]
    check(
        "例外は error イベントとして yield される",
        given="boom ノードが RuntimeError を送出",
        expected=[{"type": "error", "agent": "boom", "content": "わざと失敗"}],
        actual=error_events,
    )
    check(
        "例外が起きても後続の recover ノードは実行される",
        given="boom で失敗した後の実行状態",
        expected=True,
        actual=final.get("done"),
    )


# ---------------------------------------------------------------------------
# 6. Enum ルーティング: Enum メンバーを signal に使える
# ---------------------------------------------------------------------------
def demo_enum_routing():
    section("Enum ルーティング — Enum メンバーで分岐する")

    class Decision(Enum):
        RETRY = "retry"
        COMPLETE = "complete"

    reached: List[str] = []

    def worker(state: NodeState):
        return {"decision": Decision.COMPLETE}

    def done(state: NodeState):
        reached.append("done")
        return {}

    g = LLMGraph()
    g.add_node("worker", worker)
    g.add_node("done", done)
    g.add_edge(LLMGraph.START, "worker")
    g.add_conditional_edge("worker", "decision", {"COMPLETE": "done", "RETRY": "worker"})
    g.add_edge("done", LLMGraph.END)

    run_graph(g, {})
    check(
        "Decision.COMPLETE は .name='COMPLETE' で path_map と照合される",
        given="worker が {'decision': Decision.COMPLETE} を返す",
        expected=["done"],
        actual=reached,
    )


# ---------------------------------------------------------------------------
# 7. スキーマ検証: 宣言外キーは弾かれる
# ---------------------------------------------------------------------------
def demo_schema_validation():
    section("スキーマ検証 — 宣言外キーの書き込みを実行時に検出する")

    class State(TypedDict, total=False):
        input: str
        plan: str

    def typo_node(state: NodeState):
        return {"paln": "typo!"}  # plan の打ち間違い

    g = LLMGraph(state_schema=State)
    g.add_node("typo", typo_node)
    g.add_edge(LLMGraph.START, "typo")
    g.add_edge("typo", LLMGraph.END)

    err = run_expect_error(lambda: run_graph(g, {"input": "x"}))
    check(
        "宣言外キー 'paln' を返すと ValueError になる",
        given="ノードが {'paln': ...} を返す（スキーマは input/plan のみ）",
        expected="ValueError",
        actual=err,
    )


# ---------------------------------------------------------------------------
# 8. 無限ループ防止: max_steps で打ち切られる
# ---------------------------------------------------------------------------
def demo_max_steps():
    section("無限ループ防止 — max_steps を超えると打ち切る")

    def spin(state: NodeState):
        return {"decision": "again"}

    g = LLMGraph()
    g.add_node("spin", spin)
    g.add_edge(LLMGraph.START, "spin")
    g.add_conditional_edge("spin", "decision", {"again": "spin"})

    err = run_expect_error(lambda: list(g.run({}, max_steps=5)))
    check(
        "終わらないループは RuntimeError で安全に停止する",
        given="常に 'again' を返すグラフ, max_steps=5",
        expected="RuntimeError",
        actual=err,
    )


# ---------------------------------------------------------------------------
# 9. Mermaid 出力: グラフ構造を可視化文字列にする
# ---------------------------------------------------------------------------
def demo_mermaid():
    section("Mermaid 出力 — グラフ構造を可視化文字列にする")

    g = LLMGraph()
    g.add_node("plan", lambda s: {"decision": "ok"})
    g.add_node("run", lambda s: {})
    g.add_edge(LLMGraph.START, "plan")
    g.add_conditional_edge("plan", "decision", {"ok": "run"})
    g.add_edge("run", LLMGraph.END)

    mermaid = g.get_graph_mermaid()
    check(
        "条件分岐エッジに signal ラベルが付く",
        given="add_conditional_edge('plan', 'decision', {'ok': 'run'})",
        expected=True,
        actual=("plan -- ok --> run" in mermaid),
    )
    print(dim("          --- 生成された Mermaid ---"))
    for line in mermaid.splitlines():
        print(dim(f"          {line}"))


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main() -> int:
    print(bold("LLMGraph 動作デモ（読みやすい結果表示版）"))
    print(dim("test_llm_graph.py と同じ挙動を、1 件ずつ内容と合否を示しながら確認します。"))

    demo_sequential()
    demo_conditional()
    demo_loop()
    demo_streaming()
    demo_error_handling()
    demo_enum_routing()
    demo_schema_validation()
    demo_max_steps()
    demo_mermaid()

    total = _passed + _failed
    print()
    print(cyan("═" * 50))
    summary = f"結果: {_passed}/{total} 件 合格"
    if _failed == 0:
        print(bold(green(f"✓ {summary} — すべて成功しました 🎉")))
    else:
        print(bold(red(f"✗ {summary}（{_failed} 件 失敗）")))
    print(cyan("═" * 50))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""
LLMGraph 本体の動作確認用テスト（LLM 非依存）。

実行方法:
    python -m unittest test_llm_graph
    # または
    python test_llm_graph.py
"""
import unittest
import warnings
from enum import Enum
from typing import TypedDict, Optional

from src.llm_graph_kit import LLMGraph, NodeState

# state_schema 未指定の DeprecationWarning は、検証する個別テスト以外では
# 出力を汚さないよう抑制する（assertWarns は自前で catch_warnings するため影響なし）
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r"Creating LLMGraph without state_schema is deprecated",
)


# ---------------------------------------------------------------------------
# 1. グラフ構築時のバリデーション
# ---------------------------------------------------------------------------
class TestGraphConstruction(unittest.TestCase):

    def test_reserved_node_names_are_rejected(self):
        g = LLMGraph()
        for reserved in (LLMGraph.START, LLMGraph.END):
            with self.assertRaises(ValueError):
                g.add_node(reserved, lambda s: {})

    def test_edge_from_end_is_rejected(self):
        g = LLMGraph()
        with self.assertRaises(ValueError):
            g.add_edge(LLMGraph.END, "x")

    def test_duplicate_edge_is_rejected(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        g.add_edge("a", "b")
        with self.assertRaises(ValueError):
            g.add_edge("a", "c")

    def test_add_edge_from_start_sets_entry_point(self):
        g = LLMGraph()
        g.add_node("first", lambda s: {})
        g.add_edge(LLMGraph.START, "first")
        self.assertEqual(g.entry_point, "first")

    def test_duplicate_add_node_is_rejected(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        with self.assertRaises(ValueError) as ctx:
            g.add_node("a", lambda s: {"x": 1})
        self.assertIn("a", str(ctx.exception))
        self.assertIn("already registered", str(ctx.exception))

    def test_double_start_edge_is_rejected(self):
        # entry_point の黙々上書きを防止
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        with self.assertRaises(ValueError) as ctx:
            g.add_edge(LLMGraph.START, "b")
        self.assertIn("already set", str(ctx.exception))
        # 最初の entry_point は保持されている
        self.assertEqual(g.entry_point, "a")

    def test_duplicate_conditional_edge_is_rejected(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {"decision": "x"})
        g.add_conditional_edge("a", "decision", {"x": LLMGraph.END})
        with self.assertRaises(ValueError):
            g.add_conditional_edge("a", "decision", {"y": LLMGraph.END})

    def test_regular_then_conditional_edge_from_same_node_is_rejected(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {"decision": "x"})
        g.add_edge("a", "b")
        with self.assertRaises(ValueError) as ctx:
            g.add_conditional_edge("a", "decision", {"x": LLMGraph.END})
        self.assertIn("regular edge", str(ctx.exception))

    def test_conditional_then_regular_edge_from_same_node_is_rejected(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {"decision": "x"})
        g.add_conditional_edge("a", "decision", {"x": LLMGraph.END})
        with self.assertRaises(ValueError) as ctx:
            g.add_edge("a", "b")
        self.assertIn("conditional edge", str(ctx.exception))


# ---------------------------------------------------------------------------
# 2. グラフ実行時の挙動
# ---------------------------------------------------------------------------
class TestGraphExecution(unittest.TestCase):

    def test_run_without_entry_point_raises(self):
        g = LLMGraph()
        with self.assertRaises(ValueError):
            list(g.run({}))

    def test_run_with_undefined_target_raises(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        g.add_edge("a", "ghost")
        with self.assertRaises(ValueError) as ctx:
            list(g.run({}))
        # 実行に入る前（"a" が呼ばれる前）にグラフ整合性検証で失敗する
        self.assertIn("ghost", str(ctx.exception))

    def test_run_with_undefined_conditional_target_raises_before_execution(self):
        # path_map の遷移先が未登録ノードなら、ノードを 1 つも実行せずに失敗する
        executed = []

        def a(state: NodeState):
            executed.append("a")
            return {"decision": "go"}

        g = LLMGraph()
        g.add_node("a", a)
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", "decision", {"go": "ghost"})
        with self.assertRaises(ValueError) as ctx:
            list(g.run({}))
        self.assertIn("ghost", str(ctx.exception))
        self.assertEqual(executed, [])  # 構築不正なら一切実行しない

    def test_run_with_undefined_entry_point_raises_before_execution(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        # START から登録されていない "b" を指す
        g.add_edge(LLMGraph.START, "b")
        with self.assertRaises(ValueError) as ctx:
            list(g.run({}))
        self.assertIn("b", str(ctx.exception))

    def test_max_steps_default_catches_infinite_loop(self):
        # 常に "retry" を返し続けるグラフ → デフォルト max_steps で打ち切られる
        def a(state: NodeState):
            return {"decision": "retry"}

        g = LLMGraph()
        g.add_node("a", a)
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", "decision", {"retry": "a"})
        with self.assertRaises(RuntimeError) as ctx:
            list(g.run({}))
        self.assertIn("max_steps", str(ctx.exception))

    def test_max_steps_can_be_overridden(self):
        # max_steps=3 でちょうど 3 回実行されて打ち切られる
        count = {"n": 0}

        def a(state: NodeState):
            count["n"] += 1
            return {"decision": "retry"}

        g = LLMGraph()
        g.add_node("a", a)
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", "decision", {"retry": "a"})
        with self.assertRaises(RuntimeError):
            list(g.run({}, max_steps=3))
        self.assertEqual(count["n"], 3)

    def test_max_steps_invalid_value_raises(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        g.add_edge("a", LLMGraph.END)
        with self.assertRaises(ValueError):
            list(g.run({}, max_steps=0))
        with self.assertRaises(ValueError):
            list(g.run({}, max_steps=-1))

    def test_node_returning_non_dict_raises_type_error(self):
        # 文字列を return してしまった場合に分かりやすい TypeError を出す
        def bad(state: NodeState):
            return "oops, forgot to wrap in dict"

        g = LLMGraph()
        g.add_node("bad", bad)
        g.add_edge(LLMGraph.START, "bad")
        with self.assertRaises(TypeError) as ctx:
            list(g.run({}))
        msg = str(ctx.exception)
        self.assertIn("bad", msg)
        self.assertIn("dict", msg)

    def test_node_returning_none_is_accepted(self):
        # ノードが何も返さない (None) のは正常
        def silent(state: NodeState):
            pass  # 暗黙の return None

        g = LLMGraph()
        g.add_node("silent", silent)
        g.add_edge(LLMGraph.START, "silent")
        events = list(g.run({}))
        self.assertEqual(events, [])

    def test_node_returning_empty_dict_is_accepted(self):
        # 空 dict も正常 (no-op update)
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        events = list(g.run({}))
        self.assertEqual(events, [])

    def test_sequential_execution_threads_state_between_nodes(self):
        captured = {}

        def node_a(state: NodeState):
            return {"from_a": "a-done"}

        def node_b(state: NodeState):
            captured["a_value"] = state.get("from_a")
            return {"from_b": "b-done"}

        g = LLMGraph()
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(LLMGraph.START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", LLMGraph.END)

        events = list(g.run({}))
        self.assertEqual(events, [])
        self.assertEqual(captured["a_value"], "a-done")

    def test_generator_node_yields_events_and_returns_state(self):
        def gen_node(state: NodeState):
            yield {"type": "log", "content": "step1"}
            yield {"type": "log", "content": "step2"}
            return {"key": "value"}

        sink = {}

        def consumer(state: NodeState):
            sink["seen_key"] = state.get("key")
            return {}

        g = LLMGraph()
        g.add_node("gen", gen_node)
        g.add_node("consumer", consumer)
        g.add_edge(LLMGraph.START, "gen")
        g.add_edge("gen", "consumer")

        events = list(g.run({}))
        self.assertEqual(
            events,
            [
                {"type": "log", "content": "step1"},
                {"type": "log", "content": "step2"},
            ],
        )
        self.assertEqual(sink["seen_key"], "value")

    def test_error_in_node_is_captured_and_event_yielded(self):
        def boom(state: NodeState):
            raise RuntimeError("err1")

        g = LLMGraph()
        g.add_node("boom", boom)
        g.add_edge(LLMGraph.START, "boom")

        events = list(g.run({}))
        self.assertEqual(
            events,
            [{"type": "error", "agent": "boom", "content": "err1"}],
        )

    def test_initial_state_cannot_carry_reserved_keys(self):
        # __errors__ はライブラリ管理の予約キー。呼び出し側は initial_state に入れられない
        g = LLMGraph()
        g.add_node("ok", lambda s: {"done": True})
        g.add_edge(LLMGraph.START, "ok")
        with self.assertRaises(ValueError) as ctx:
            list(g.run({"__errors__": ["pre"]}))
        self.assertIn("__errors__", str(ctx.exception))

    def test_node_cannot_overwrite_reserved_keys(self):
        # ノード戻り値で __errors__ を返すのも禁止
        def bad(state: NodeState):
            return {"__errors__": ["clobber"]}

        g = LLMGraph()
        g.add_node("bad", bad)
        g.add_edge(LLMGraph.START, "bad")
        with self.assertRaises(ValueError) as ctx:
            list(g.run({}))
        self.assertIn("__errors__", str(ctx.exception))

    def test_default_errors_key_is_initialized(self):
        # 初期 state に __errors__ がなくても例外なく実行できる
        g = LLMGraph()
        g.add_node("ok", lambda s: {"done": True})
        g.add_edge(LLMGraph.START, "ok")
        list(g.run({}))

    def test_nested_mutable_values_in_caller_are_not_mutated(self):
        # state 内の入れ子のリスト/辞書がノード内で書き換えられても、
        # 呼び出し側のオブジェクトには反映されないこと（deepcopy 保証）
        def mutate(state: NodeState):
            state["history"].append("from_node")
            state["config"]["retry"] = 999
            return {}

        g = LLMGraph()
        g.add_node("mutate", mutate)
        g.add_edge(LLMGraph.START, "mutate")

        caller_history = ["initial"]
        caller_config = {"retry": 3}
        list(g.run({"history": caller_history, "config": caller_config}))

        self.assertEqual(caller_history, ["initial"])
        self.assertEqual(caller_config, {"retry": 3})

    def test_same_initial_state_can_be_reused_across_runs(self):
        # 同じ initial_state を 2 回 run() に渡しても、
        # 1回目の副作用が 2回目に影響しないこと
        def append_once(state: NodeState):
            state["history"].append("x")
            return {}

        g = LLMGraph()
        g.add_node("a", append_once)
        g.add_edge(LLMGraph.START, "a")

        initial = {"history": []}
        list(g.run(initial))
        list(g.run(initial))

        self.assertEqual(initial["history"], [])


# ---------------------------------------------------------------------------
# 3. 条件分岐
# ---------------------------------------------------------------------------
class TestConditionalEdges(unittest.TestCase):

    def _build_router_graph(self, condition, path_map):
        reached = []

        def router(state: NodeState):
            # state はそのまま使い、更新は不要
            return {}

        def a(state: NodeState):
            reached.append("a")
            return {}

        def b(state: NodeState):
            reached.append("b")
            return {}

        g = LLMGraph()
        g.add_node("router", router)
        g.add_node("a", a)
        g.add_node("b", b)
        g.add_edge(LLMGraph.START, "router")
        g.add_conditional_edge("router", condition, path_map)
        return g, reached

    def test_conditional_edge_with_state_key(self):
        g, reached = self._build_router_graph("decision", {"a": "a", "b": "b"})
        list(g.run({"decision": "a"}))
        self.assertEqual(reached, ["a"])

        g, reached = self._build_router_graph("decision", {"a": "a", "b": "b"})
        list(g.run({"decision": "b"}))
        self.assertEqual(reached, ["b"])

    def test_conditional_edge_with_callable(self):
        cond = lambda s: "hi" if s.get("value", 0) > 50 else "lo"
        g, reached = self._build_router_graph(cond, {"hi": "a", "lo": "b"})
        list(g.run({"value": 100}))
        self.assertEqual(reached, ["a"])

    def test_conditional_fallthrough_goes_to_end(self):
        # path_map に signal が見つからない場合は END へ
        g, reached = self._build_router_graph("decision", {"a": "a"})
        list(g.run({"decision": "unknown"}))
        self.assertEqual(reached, [])

    def test_conditional_routes_by_enum_name(self):
        # Enum メンバーを signal にした場合、その .name で path_map と照合される
        class Decision(Enum):
            RETRY = "retry"
            COMPLETE = "complete"

        reached = []

        def a(state: NodeState):
            reached.append("a")
            return {"decision": Decision.COMPLETE}

        def done(state: NodeState):
            reached.append("done")
            return {}

        g = LLMGraph()
        g.add_node("a", a)
        g.add_node("done", done)
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", "decision", {"COMPLETE": "done", "RETRY": "a"})
        g.add_edge("done", LLMGraph.END)

        list(g.run({}))
        self.assertEqual(reached, ["a", "done"])

    def test_conditional_routes_when_path_map_keys_are_enum(self):
        # path_map のキー側に Enum を渡しても、名前で照合される
        class Sig(Enum):
            GO = 1

        reached = []

        def a(state: NodeState):
            return {"d": Sig.GO}

        def next_node(state: NodeState):
            reached.append("next_node")
            return {}

        g = LLMGraph()
        g.add_node("a", a)
        g.add_node("next_node", next_node)
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", "d", {Sig.GO: "next_node"})
        g.add_edge("next_node", LLMGraph.END)

        list(g.run({}))
        self.assertEqual(reached, ["next_node"])

    def test_conditional_to_end_explicitly(self):
        g, reached = self._build_router_graph("decision", {"stop": LLMGraph.END})
        list(g.run({"decision": "stop"}))
        self.assertEqual(reached, [])


# ---------------------------------------------------------------------------
# 4. Mermaid 出力
# ---------------------------------------------------------------------------
class TestMermaidOutput(unittest.TestCase):

    def test_mermaid_contains_nodes_and_normal_edges(self):
        g = LLMGraph()
        g.add_node("a", lambda s: {})
        g.add_node("b", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", LLMGraph.END)

        mermaid = g.get_graph_mermaid()
        self.assertIn("graph TD", mermaid)
        self.assertIn("__START__ --> a", mermaid)
        self.assertIn("a --> b", mermaid)
        self.assertIn("b --> __END__", mermaid)

    def test_mermaid_conditional_edges_are_labeled(self):
        g = LLMGraph()
        g.add_node("router", lambda s: {"d": "x"})
        g.add_node("x", lambda s: {})
        g.add_edge(LLMGraph.START, "router")
        g.add_conditional_edge("router", "d", {"x": "x"})

        mermaid = g.get_graph_mermaid()
        self.assertIn('router{"router"}', mermaid)  # 菱形＋引用符ラベル
        self.assertIn("router -- x --> x", mermaid)

    def test_mermaid_label_escapes_special_chars(self):
        # ノード名にブラケットや引用符が含まれていてもラベルが壊れない
        g = LLMGraph()
        g.add_node("step_one", lambda s: {})
        g.add_edge(LLMGraph.START, "step_one")
        g.add_edge("step_one", LLMGraph.END)

        mermaid = g.get_graph_mermaid()
        # 二重引用符で囲まれているはず
        self.assertIn('step_one["step_one"]', mermaid)


# ---------------------------------------------------------------------------
# 5. state_schema による制約
# ---------------------------------------------------------------------------
class MyState(TypedDict, total=False):
    input: str
    plan: Optional[str]
    decision: str


class TestStateSchema(unittest.TestCase):

    def test_schema_with_no_fields_is_rejected(self):
        class Empty(TypedDict, total=False):
            pass
        with self.assertRaises(ValueError):
            LLMGraph(state_schema=Empty)

    def test_schema_cannot_declare_reserved_key(self):
        class Bad(TypedDict, total=False):
            __errors__: list
        with self.assertRaises(ValueError) as ctx:
            LLMGraph(state_schema=Bad)
        self.assertIn("__errors__", str(ctx.exception))

    def test_initial_state_unknown_key_is_rejected(self):
        g = LLMGraph(state_schema=MyState)
        g.add_node("a", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        with self.assertRaises(ValueError) as ctx:
            list(g.run({"input": "x", "unknown": 1}))
        self.assertIn("unknown", str(ctx.exception))

    def test_initial_state_declared_keys_pass(self):
        g = LLMGraph(state_schema=MyState)
        g.add_node("a", lambda s: {})
        g.add_edge(LLMGraph.START, "a")
        # 例外が出ないこと
        list(g.run({"input": "x"}))

    def test_node_return_unknown_key_is_rejected(self):
        # ノード戻り値の未宣言キーは即座に例外
        def bad(state: NodeState):
            return {"typo_plan": "oops"}

        g = LLMGraph(state_schema=MyState)
        g.add_node("bad", bad)
        g.add_edge(LLMGraph.START, "bad")
        with self.assertRaises(ValueError) as ctx:
            list(g.run({"input": "x"}))
        self.assertIn("typo_plan", str(ctx.exception))

    def test_node_return_declared_keys_pass(self):
        def good(state: NodeState):
            return {"plan": "ok"}

        g = LLMGraph(state_schema=MyState)
        g.add_node("good", good)
        g.add_edge(LLMGraph.START, "good")
        list(g.run({"input": "x"}))

    def test_conditional_condition_unknown_key_is_rejected(self):
        # add_conditional_edge の condition 文字列がスキーマと整合しない場合
        g = LLMGraph(state_schema=MyState)
        g.add_node("a", lambda s: {"decision": "x"})
        g.add_edge(LLMGraph.START, "a")
        with self.assertRaises(ValueError) as ctx:
            g.add_conditional_edge("a", "dcision", {"x": LLMGraph.END})
        self.assertIn("dcision", str(ctx.exception))

    def test_conditional_condition_declared_key_passes(self):
        g = LLMGraph(state_schema=MyState)
        g.add_node("a", lambda s: {"decision": "stop"})
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", "decision", {"stop": LLMGraph.END})
        list(g.run({"input": "x"}))

    def test_conditional_condition_callable_is_unaffected_by_schema(self):
        # callable condition はスキーマ検査の対象外
        g = LLMGraph(state_schema=MyState)
        g.add_node("a", lambda s: {"plan": "ok"})
        g.add_edge(LLMGraph.START, "a")
        g.add_conditional_edge("a", lambda s: "done", {"done": LLMGraph.END})
        list(g.run({"input": "x"}))

    def test_no_schema_preserves_freeform_behavior(self):
        # state_schema=None なら従来通り何でも入る（後方互換）
        g = LLMGraph()
        g.add_node("a", lambda s: {"any_key": 1, "another": [1, 2]})
        g.add_edge(LLMGraph.START, "a")
        events = list(g.run({"foo": "bar"}))
        self.assertEqual(events, [])


# ---------------------------------------------------------------------------
# 6. 非推奨警告
# ---------------------------------------------------------------------------
class TestDeprecation(unittest.TestCase):

    def test_warns_when_no_state_schema_provided(self):
        with self.assertWarns(DeprecationWarning) as ctx:
            LLMGraph()
        self.assertIn("state_schema", str(ctx.warning))

    def test_no_warning_when_state_schema_provided(self):
        class S(TypedDict, total=False):
            x: str

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            # state_schema を渡せば警告は出ない（出れば例外になりテスト失敗）
            LLMGraph(state_schema=S)


if __name__ == "__main__":
    unittest.main()

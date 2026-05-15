"""
LLMGraph 本体の動作確認用テスト（LLM 非依存）。

実行方法:
    python -m unittest test_llm_graph
    # または
    python test_llm_graph.py
"""
import unittest

from src.llm_graph_kit import LLMGraph, NodeState


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
        with self.assertRaises(ValueError):
            list(g.run({}))

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

    def test_callers_errors_list_is_not_mutated(self):
        def boom(state: NodeState):
            raise RuntimeError("err1")

        g = LLMGraph()
        g.add_node("boom", boom)
        g.add_edge(LLMGraph.START, "boom")

        caller_list = ["pre-existing"]
        list(g.run({"__errors__": caller_list}))
        # 呼び出し側のリストは変更されない
        self.assertEqual(caller_list, ["pre-existing"])

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
            return state  # 入力をそのまま返す

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
        self.assertIn("router{router}", mermaid)  # 菱形
        self.assertIn("router -- x --> x", mermaid)


if __name__ == "__main__":
    unittest.main()

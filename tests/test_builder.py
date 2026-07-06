"""
GUI 仕様 (JSON) -> LLMGraph 変換 (builder.py) のテスト。

フロントエンドが送る spec からノード関数のコンパイル・グラフ構築・
条件分岐の組み立てが正しく行われるかを検証する。LLM 非依存。

実行方法:
    python tests/test_builder.py          # 単体で実行
    python run_tests.py                    # 全テストをまとめて実行
"""
import os
import sys
import unittest

# tests/ から実行してもリポジトリ直下の `src` パッケージを解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm_graph_kit.gui.builder import (
    START_ID,
    END_ID,
    build_state_schema,
    build_graph_from_spec,
    parse_initial_state,
)


def _drain(graph, initial_state):
    """run() のジェネレータを回して (events, final_state) を返すヘルパー。"""
    gen = graph.run(initial_state)
    events = []
    final_state = None
    try:
        while True:
            events.append(next(gen))
    except StopIteration as e:
        final_state = e.value
    return events, final_state


# ---------------------------------------------------------------------------
# 1. build_state_schema
# ---------------------------------------------------------------------------
class TestBuildStateSchema(unittest.TestCase):

    def test_empty_fields_returns_none(self):
        self.assertIsNone(build_state_schema([]))

    def test_fields_with_only_empty_names_returns_none(self):
        # name が空のフィールドはスキップされ、結果的にアノテーションが空になる
        self.assertIsNone(build_state_schema([{"name": "", "type": "str"}]))

    def test_valid_fields_produce_typeddict(self):
        schema = build_state_schema(
            [
                {"name": "input", "type": "str"},
                {"name": "count", "type": "int"},
                {"name": "items", "type": "list"},
            ]
        )
        self.assertIsNotNone(schema)
        ann = schema.__annotations__
        self.assertEqual(ann["input"], str)
        self.assertEqual(ann["count"], int)
        self.assertEqual(ann["items"], list)

    def test_type_defaults_to_any_when_missing(self):
        schema = build_state_schema([{"name": "x"}])
        # "Any" は object にマップされる
        self.assertEqual(schema.__annotations__["x"], object)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError) as ctx:
            build_state_schema([{"name": "x", "type": "complex"}])
        self.assertIn("complex", str(ctx.exception))

    def test_schema_is_usable_by_llmgraph(self):
        # 生成した TypedDict をそのまま LLMGraph に渡して機能すること
        schema = build_state_schema([{"name": "input", "type": "str"}])
        graph = build_graph_from_spec(
            {
                "state_schema": [{"name": "input", "type": "str"}],
                "nodes": [
                    {
                        "id": "n1",
                        "kind": "function",
                        "name": "echo",
                        "code": "def echo(state):\n    return {'input': state['input']}",
                    }
                ],
                "edges": [
                    {"source": START_ID, "target": "n1"},
                    {"source": "n1", "target": END_ID},
                ],
            }
        )
        # 宣言外キーは弾かれる
        with self.assertRaises(ValueError):
            _drain(graph, {"unknown": 1})


# ---------------------------------------------------------------------------
# 2. parse_initial_state
# ---------------------------------------------------------------------------
class TestParseInitialState(unittest.TestCase):

    def test_missing_key_returns_empty_dict(self):
        self.assertEqual(parse_initial_state({}), {})

    def test_none_returns_empty_dict(self):
        self.assertEqual(parse_initial_state({"initial_state": None}), {})

    def test_dict_passes_through(self):
        self.assertEqual(
            parse_initial_state({"initial_state": {"a": 1}}), {"a": 1}
        )

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parse_initial_state({"initial_state": [1, 2, 3]})
        self.assertIn("object", str(ctx.exception))


# ---------------------------------------------------------------------------
# 3. build_graph_from_spec: 正常系
# ---------------------------------------------------------------------------
class TestBuildGraphHappyPath(unittest.TestCase):

    def test_simple_function_graph_runs(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "function",
                    "name": "produce",
                    "code": "def produce(state):\n    return {'value': 42}",
                }
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": END_ID},
            ],
        }
        graph = build_graph_from_spec(spec)
        self.assertEqual(graph.entry_point, "produce")
        _events, final = _drain(graph, {})
        self.assertEqual(final["value"], 42)

    def test_function_name_can_differ_from_node_name(self):
        # code 内の def 名がノード名と一致しなくても、最後の callable を拾う
        spec = {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "function",
                    "name": "step",
                    "code": "def totally_different(state):\n    return {'ok': True}",
                }
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": END_ID},
            ],
        }
        graph = build_graph_from_spec(spec)
        _events, final = _drain(graph, {})
        self.assertTrue(final["ok"])

    def test_conditional_key_condition_routes(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "function",
                    "name": "router",
                    "code": "def router(state):\n    return {'decision': state.get('decision', 'a')}",
                },
                {
                    "id": "n2",
                    "kind": "conditional",
                    "name": "branch",
                    "code": "",
                    "condition_kind": "key",
                    "condition_value": "decision",
                },
                {
                    "id": "n3",
                    "kind": "function",
                    "name": "path_a",
                    "code": "def path_a(state):\n    return {'taken': 'a'}",
                },
                {
                    "id": "n4",
                    "kind": "function",
                    "name": "path_b",
                    "code": "def path_b(state):\n    return {'taken': 'b'}",
                },
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3", "signal": "a"},
                {"source": "n2", "target": "n4", "signal": "b"},
                {"source": "n3", "target": END_ID},
                {"source": "n4", "target": END_ID},
            ],
        }
        graph = build_graph_from_spec(spec)
        _events, final = _drain(graph, {"decision": "b"})
        self.assertEqual(final["taken"], "b")

    def test_conditional_code_lambda_condition(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "conditional",
                    "name": "branch",
                    "code": "",
                    "condition_kind": "code",
                    "condition_value": "lambda state: 'hi' if state.get('v', 0) > 10 else 'lo'",
                },
                {
                    "id": "n2",
                    "kind": "function",
                    "name": "high",
                    "code": "def high(state):\n    return {'r': 'high'}",
                },
                {
                    "id": "n3",
                    "kind": "function",
                    "name": "low",
                    "code": "def low(state):\n    return {'r': 'low'}",
                },
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2", "signal": "hi"},
                {"source": "n1", "target": "n3", "signal": "lo"},
                {"source": "n2", "target": END_ID},
                {"source": "n3", "target": END_ID},
            ],
        }
        graph = build_graph_from_spec(spec)
        _events, final = _drain(graph, {"v": 100})
        self.assertEqual(final["r"], "high")

    def test_conditional_code_def_condition(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "conditional",
                    "name": "branch",
                    "code": "",
                    "condition_kind": "code",
                    "condition_value": "def decide(state):\n    return 'go'",
                },
                {
                    "id": "n2",
                    "kind": "function",
                    "name": "target",
                    "code": "def target(state):\n    return {'done': True}",
                },
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2", "signal": "go"},
                {"source": "n2", "target": END_ID},
            ],
        }
        graph = build_graph_from_spec(spec)
        _events, final = _drain(graph, {})
        self.assertTrue(final["done"])

    def test_conditional_with_body_code_executes(self):
        # conditional ノードに本体コードがあれば通常ノードとして実行される
        spec = {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "conditional",
                    "name": "branch",
                    "code": "def branch(state):\n    return {'decision': 'end'}",
                    "condition_kind": "key",
                    "condition_value": "decision",
                },
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": END_ID, "signal": "end"},
            ],
        }
        graph = build_graph_from_spec(spec)
        _events, final = _drain(graph, {})
        self.assertEqual(final["decision"], "end")


# ---------------------------------------------------------------------------
# 4. build_graph_from_spec: 異常系
# ---------------------------------------------------------------------------
class TestBuildGraphErrors(unittest.TestCase):

    def _base_conditional_spec(self):
        return {
            "state_schema": [],
            "nodes": [
                {
                    "id": "n1",
                    "kind": "conditional",
                    "name": "branch",
                    "code": "",
                    "condition_kind": "key",
                    "condition_value": "decision",
                },
                {
                    "id": "n2",
                    "kind": "function",
                    "name": "target",
                    "code": "def target(state):\n    return {}",
                },
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2", "signal": "go"},
                {"source": "n2", "target": END_ID},
            ],
        }

    def test_empty_node_name_raises(self):
        spec = {
            "state_schema": [],
            "nodes": [{"id": "n1", "kind": "function", "name": "  ", "code": "x"}],
            "edges": [],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("empty name", str(ctx.exception))

    def test_duplicate_node_name_raises(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "function", "name": "dup",
                 "code": "def dup(state):\n    return {}"},
                {"id": "n2", "kind": "function", "name": "dup",
                 "code": "def dup(state):\n    return {}"},
            ],
            "edges": [],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("Duplicate node name", str(ctx.exception))

    def test_empty_code_function_raises(self):
        spec = {
            "state_schema": [],
            "nodes": [{"id": "n1", "kind": "function", "name": "x", "code": ""}],
            "edges": [{"source": START_ID, "target": "n1"}],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("empty code", str(ctx.exception))

    def test_function_code_without_callable_raises(self):
        spec = {
            "state_schema": [],
            "nodes": [{"id": "n1", "kind": "function", "name": "x", "code": "y = 1"}],
            "edges": [{"source": START_ID, "target": "n1"}],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("must define a function", str(ctx.exception))

    def test_syntax_error_in_code_raises_valueerror(self):
        spec = {
            "state_schema": [],
            "nodes": [{"id": "n1", "kind": "function", "name": "x",
                       "code": "def x(state):\n    return {"}],
            "edges": [{"source": START_ID, "target": "n1"}],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("Syntax error", str(ctx.exception))

    def test_conditional_edge_without_signal_raises(self):
        spec = self._base_conditional_spec()
        spec["edges"][1].pop("signal")  # n1 -> n2 の signal を消す
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("must have a signal", str(ctx.exception))

    def test_conditional_without_outgoing_edges_raises(self):
        spec = self._base_conditional_spec()
        # conditional の出力エッジを除去（n2 は START から張ってつなぐ）
        spec["edges"] = [
            {"source": START_ID, "target": "n2"},
            {"source": "n2", "target": END_ID},
        ]
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("no outgoing edges", str(ctx.exception))

    def test_conditional_empty_condition_value_raises(self):
        spec = self._base_conditional_spec()
        spec["nodes"][0]["condition_value"] = ""
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("empty condition_value", str(ctx.exception))

    def test_unknown_condition_kind_raises(self):
        spec = self._base_conditional_spec()
        spec["nodes"][0]["condition_kind"] = "magic"
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("Unknown condition_kind", str(ctx.exception))

    def test_unknown_node_kind_raises(self):
        spec = {
            "state_schema": [],
            "nodes": [{"id": "n1", "kind": "wormhole", "name": "x", "code": ""}],
            "edges": [],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("Unknown node kind", str(ctx.exception))

    def test_edge_referencing_unknown_node_id_raises(self):
        spec = {
            "state_schema": [],
            "nodes": [{"id": "n1", "kind": "function", "name": "x",
                       "code": "def x(state):\n    return {}"}],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "ghost"},
            ],
        }
        with self.assertRaises(ValueError) as ctx:
            build_graph_from_spec(spec)
        self.assertIn("ghost", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

"""
GUI 仕様 (JSON) -> Python ソース生成 (codegen.py) のテスト。

生成コードが構文的に正しく、実際に import して build_graph() / run() まで
通ることを検証する。LLM 非依存。

実行方法:
    python tests/test_codegen.py          # 単体で実行
    python run_tests.py                    # 全テストをまとめて実行
"""
import os
import sys
import types
import unittest

# tests/ から実行してもリポジトリ直下の `src` パッケージを解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm_graph_kit.gui.builder import START_ID, END_ID
from src.llm_graph_kit.gui.codegen import (
    generate_python_code,
    _sanitize_id,
    _extract_def_name,
)


def _exec_generated(code: str) -> types.ModuleType:
    """生成コードをモジュールとして実行し、名前空間を返す。

    生成コードは `from llm_graph_kit import ...` を含むが、本リポジトリでは
    パッケージが `src.llm_graph_kit` にあるため、実行前に別名を通しておく。
    """
    import src.llm_graph_kit as pkg
    sys.modules.setdefault("llm_graph_kit", pkg)

    mod = types.ModuleType("generated_graph")
    compiled = compile(code, "<generated>", "exec")
    exec(compiled, mod.__dict__)
    return mod


# ---------------------------------------------------------------------------
# 1. ヘルパー関数
# ---------------------------------------------------------------------------
class TestHelpers(unittest.TestCase):

    def test_sanitize_id_replaces_invalid_chars(self):
        self.assertEqual(_sanitize_id("my node-1"), "my_node_1")

    def test_sanitize_id_prefixes_leading_digit(self):
        self.assertEqual(_sanitize_id("1abc"), "n_1abc")

    def test_sanitize_id_empty_becomes_prefixed(self):
        self.assertTrue(_sanitize_id("").startswith("n_"))

    def test_extract_def_name(self):
        self.assertEqual(_extract_def_name("def my_fn(state):\n    return {}"), "my_fn")

    def test_extract_def_name_with_indent(self):
        self.assertEqual(_extract_def_name("   def spaced(s): pass"), "spaced")

    def test_extract_def_name_returns_empty_for_non_def(self):
        self.assertEqual(_extract_def_name("lambda s: 1"), "")


# ---------------------------------------------------------------------------
# 2. 生成コードの構文的妥当性
# ---------------------------------------------------------------------------
class TestGeneratedCodeCompiles(unittest.TestCase):

    def test_minimal_graph_compiles(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "function", "name": "produce",
                 "code": "def produce(state):\n    return {'value': 1}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": END_ID},
            ],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        # compile が通れば構文的に妥当
        compile(code, "<test>", "exec")

    def test_no_schema_emits_state_none(self):
        spec = {"state_schema": [], "nodes": [], "edges": [], "initial_state": {}}
        code = generate_python_code(spec)
        self.assertIn("State = None", code)
        self.assertIn("g = LLMGraph()", code)

    def test_schema_emits_typeddict(self):
        spec = {
            "state_schema": [
                {"name": "input", "type": "str"},
                {"name": "count", "type": "int"},
            ],
            "nodes": [],
            "edges": [],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        self.assertIn("class State(TypedDict, total=False):", code)
        self.assertIn("input: str", code)
        self.assertIn("count: int", code)
        self.assertIn("g = LLMGraph(state_schema=State)", code)

    def test_initial_state_is_emitted(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "function", "name": "x",
                 "code": "def x(state):\n    return {}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": END_ID},
            ],
            "initial_state": {"input": "hello"},
        }
        code = generate_python_code(spec)
        self.assertIn("'input': 'hello'", code)


# ---------------------------------------------------------------------------
# 3. 条件分岐の生成
# ---------------------------------------------------------------------------
class TestConditionalCodegen(unittest.TestCase):

    def test_key_condition_emits_conditional_edge(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "conditional", "name": "branch", "code": "",
                 "condition_kind": "key", "condition_value": "decision"},
                {"id": "n2", "kind": "function", "name": "target",
                 "code": "def target(state):\n    return {}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2", "signal": "go"},
                {"source": "n2", "target": END_ID},
            ],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        self.assertIn("g.add_conditional_edge('branch', 'decision'", code)
        self.assertIn("'go': 'target'", code)

    def test_lambda_condition_is_inlined(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "conditional", "name": "branch", "code": "",
                 "condition_kind": "code",
                 "condition_value": "lambda state: 'go'"},
                {"id": "n2", "kind": "function", "name": "target",
                 "code": "def target(state):\n    return {}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2", "signal": "go"},
                {"source": "n2", "target": END_ID},
            ],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        self.assertIn("lambda state: 'go'", code)

    def test_def_condition_references_function_name(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "conditional", "name": "branch", "code": "",
                 "condition_kind": "code",
                 "condition_value": "def decide(state):\n    return 'go'"},
                {"id": "n2", "kind": "function", "name": "target",
                 "code": "def target(state):\n    return {}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2", "signal": "go"},
                {"source": "n2", "target": END_ID},
            ],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        # condition 本体が関数として書き出され、名前で参照される
        self.assertIn("def decide(state):", code)
        self.assertIn("g.add_conditional_edge('branch', decide", code)


# ---------------------------------------------------------------------------
# 4. 生成コードの実行（ラウンドトリップ）
# ---------------------------------------------------------------------------
class TestGeneratedCodeRuns(unittest.TestCase):

    def test_generated_graph_builds_and_runs(self):
        spec = {
            "state_schema": [{"name": "value", "type": "int"}],
            "nodes": [
                {"id": "n1", "kind": "function", "name": "produce",
                 "code": "def produce(state):\n    return {'value': 7}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": END_ID},
            ],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        mod = _exec_generated(code)

        graph = mod.build_graph()
        self.assertEqual(graph.entry_point, "produce")

        gen = graph.run({})
        final = None
        try:
            while True:
                next(gen)
        except StopIteration as e:
            final = e.value
        self.assertEqual(final["value"], 7)

    def test_generated_conditional_graph_routes(self):
        spec = {
            "state_schema": [],
            "nodes": [
                {"id": "n1", "kind": "function", "name": "seed",
                 "code": "def seed(state):\n    return {'decision': 'b'}"},
                {"id": "n2", "kind": "conditional", "name": "branch", "code": "",
                 "condition_kind": "key", "condition_value": "decision"},
                {"id": "n3", "kind": "function", "name": "path_a",
                 "code": "def path_a(state):\n    return {'taken': 'a'}"},
                {"id": "n4", "kind": "function", "name": "path_b",
                 "code": "def path_b(state):\n    return {'taken': 'b'}"},
            ],
            "edges": [
                {"source": START_ID, "target": "n1"},
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3", "signal": "a"},
                {"source": "n2", "target": "n4", "signal": "b"},
                {"source": "n3", "target": END_ID},
                {"source": "n4", "target": END_ID},
            ],
            "initial_state": {},
        }
        code = generate_python_code(spec)
        mod = _exec_generated(code)

        gen = mod.build_graph().run({})
        final = None
        try:
            while True:
                next(gen)
        except StopIteration as e:
            final = e.value
        self.assertEqual(final["taken"], "b")


if __name__ == "__main__":
    unittest.main()

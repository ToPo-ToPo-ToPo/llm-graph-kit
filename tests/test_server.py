"""
GUI サーバー (server.py) のテスト。

fastapi が入っていない環境ではスキップされる（gui エクストラは任意）。
純粋なヘルパー関数 (_sse / _make_safe) と create_app() の構築を検証する。

実行方法:
    python tests/test_server.py           # 単体で実行
    python run_tests.py                    # 全テストをまとめて実行
"""
import os
import sys
import unittest

# tests/ から実行してもリポジトリ直下の `src` パッケージを解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


@unittest.skipUnless(_HAS_FASTAPI, "fastapi 未インストール（gui エクストラが必要）")
class TestServerHelpers(unittest.TestCase):

    def setUp(self):
        from src.llm_graph_kit.gui import server
        self.server = server

    def test_sse_formats_message(self):
        out = self.server._sse({"type": "log", "content": "hi"})
        self.assertTrue(out.startswith("data: "))
        self.assertTrue(out.endswith("\n\n"))
        self.assertIn('"type": "log"', out)

    def test_sse_preserves_non_ascii(self):
        out = self.server._sse({"content": "日本語"})
        self.assertIn("日本語", out)

    def test_sse_falls_back_on_unserializable(self):
        # default=str で通常は落ちないが、循環参照は json が例外を出す
        a = {}
        a["self"] = a
        out = self.server._sse(a)
        self.assertTrue(out.startswith("data: "))

    def test_make_safe_passes_primitives(self):
        self.assertEqual(self.server._make_safe(1), 1)
        self.assertEqual(self.server._make_safe("x"), "x")
        self.assertEqual(self.server._make_safe(True), True)
        self.assertIsNone(self.server._make_safe(None))

    def test_make_safe_recurses_into_containers(self):
        result = self.server._make_safe({"a": [1, 2], "b": {"c": 3}})
        self.assertEqual(result, {"a": [1, 2], "b": {"c": 3}})

    def test_make_safe_stringifies_unknown_types(self):
        class Custom:
            def __repr__(self):
                return "<custom>"
        self.assertEqual(self.server._make_safe(Custom()), "<custom>")

    def test_make_safe_coerces_dict_keys_to_str(self):
        result = self.server._make_safe({1: "a"})
        self.assertEqual(result, {"1": "a"})


@unittest.skipUnless(_HAS_FASTAPI, "fastapi 未インストール（gui エクストラが必要）")
class TestCreateApp(unittest.TestCase):

    def test_create_app_builds(self):
        from src.llm_graph_kit.gui.server import create_app
        app = create_app()
        self.assertIsNotNone(app)
        # 主要な API ルートが登録されている
        paths = {getattr(r, "path", None) for r in app.routes}
        self.assertIn("/api/mermaid", paths)
        self.assertIn("/api/codegen", paths)
        self.assertIn("/api/run", paths)


if __name__ == "__main__":
    unittest.main()

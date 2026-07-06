#!/usr/bin/env python
"""
テストランナー（pytest 不要）。

`tests/` 配下の `test_*.py` をすべて検出して unittest で実行する。
標準ライブラリのみで動くので、追加インストールは不要。

使い方:
    python run_tests.py                 # 全テストを実行
    python run_tests.py -v              # 詳細表示（各テスト名を出力）
    python run_tests.py test_builder    # 特定モジュールだけ実行
    python run_tests.py tests.test_builder.TestBuildStateSchema  # クラス単位

    # 個別ファイルを直接実行することもできる:
    python tests/test_builder.py
"""
import os
import sys
import unittest
import warnings

_ROOT = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.join(_ROOT, "tests")

# `src.llm_graph_kit` を解決できるようにリポジトリ直下を import パスへ
sys.path.insert(0, _ROOT)

# state_schema 未指定の DeprecationWarning は出力を汚すだけなので抑制する
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r"Creating LLMGraph without state_schema is deprecated",
)


def main(argv):
    verbosity = 2 if ("-v" in argv or "--verbose" in argv) else 1
    names = [a for a in argv if not a.startswith("-")]

    loader = unittest.TestLoader()
    if names:
        # 明示指定されたモジュール／クラス／メソッドをロード
        # "test_builder" のような短縮名は "tests.test_builder" に補完する
        resolved = [
            n if n.startswith("tests.") else f"tests.{n}"
            for n in names
        ]
        suite = loader.loadTestsFromNames(resolved)
    else:
        # tests/ 以下を自動検出
        suite = loader.discover(start_dir=_TESTS_DIR, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

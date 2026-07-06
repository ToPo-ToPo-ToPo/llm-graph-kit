# llm-graph-kit

LLM ベースのエージェントの動作内容を**グラフ形式**で記述・実行する軽量ライブラリです。
ノードとエッジでワークフローを宣言し、逐次実行・条件分岐・ストリーミングイベントの送出・Mermaid 可視化に対応します。

## できること
- ノードと有向エッジでワークフローを宣言的に組み立てる
- 条件分岐（`state` のキー値 / 任意の関数 / `Enum`）でルートを動的に切り替える
- ノードからの `yield` イベントが `run()` の出力にそのまま流れる（LLM のトークンストリーム等を呼び出し側へ伝搬しやすい）
- `TypedDict` でステートのキーを宣言し、未宣言キーの書き込みを実行時に検出
- ノードで発生した例外を自動で捕捉し、エラーイベントとして yield しつつ実行を継続
- `get_graph_mermaid()` でグラフ構造を Mermaid 文字列として出力
- **ブラウザベースのノードエディタ GUI**（オプション）でドラッグ&ドロップによる no-code 構築・実行・Python コード生成

## インストール

```bash
uv add llm-graph-kit
```

ライブラリ本体 (`llm_graph_kit`) は標準ライブラリのみで動作し、追加の依存はありません。

GUI（ノードエディタ）を使う場合は `gui` エクストラを追加します（詳細は [GUI の章](#gui%E3%83%8E%E3%83%BC%E3%83%89%E3%82%A8%E3%83%87%E3%82%A3%E3%82%BF) を参照）:

```bash
uv add 'llm-graph-kit[gui]'
```

開発する場合:

```bash
uv venv
source .venv/bin/activate
uv sync --extra gui
```

## テスト

テストは `tests/` にまとめてあり、**pytest 不要**（標準ライブラリの `unittest` のみ）です。
リポジトリ直下のランナーを実行します。

```bash
python run_tests.py                 # 全テストを実行
python run_tests.py -v              # 各テスト名も表示
python run_tests.py test_builder    # 特定モジュールだけ実行
python tests/test_codegen.py        # 個別ファイルを直接実行
```

| ファイル | 対象 |
| --- | --- |
| `tests/test_llm_graph.py` | `LLMGraph` 本体（構築・実行・条件分岐・スキーマ・Mermaid） |
| `tests/test_builder.py` | GUI 仕様 → `LLMGraph` 変換（`gui/builder.py`） |
| `tests/test_codegen.py` | GUI 仕様 → Python コード生成（`gui/codegen.py`） |
| `tests/test_server.py` | GUI サーバーのヘルパー（`gui/server.py`、fastapi 未導入なら自動スキップ） |

各チェックの「何を確認し、何を入力し、期待値と実測値がどうだったか」を
1 件ずつ日本語で表示する**読みやすい版**もあります（学習・デモ向け）。

```bash
python tests/demo_llm_graph.py            # 結果を 1 件ずつ表示
python tests/demo_llm_graph.py --no-color # 色なし（ログ保存時など）
```

LLM を使ったエージェントが**自然言語をストリーミング表示**する様子を見たい場合は
`tests/demo_llm_agent.py` を実行します。追加インストール不要で動くよう**モック LLM を
内蔵**しており（実 LLM は依存注入で差し替え可能）、回答→チェック→リトライの
ループが流れる様子を確認できます。

```bash
python tests/demo_llm_agent.py
```

## クイックスタート

```python
from typing import TypedDict, Optional
from llm_graph_kit import LLMGraph, NodeState


class State(TypedDict, total=False):
    input: str
    plan: Optional[str]
    output: Optional[str]
    decision: str


def plan(state: NodeState):
    yield {"type": "log", "content": f"plan for: {state['input']}"}
    return {"plan": "draft a response"}


def execute(state: NodeState):
    yield {"type": "log", "content": "executing..."}
    return {"output": f"done ({state['plan']})"}


def check(state: NodeState):
    return {"decision": "complete" if state.get("output") else "retry"}


workflow = LLMGraph(state_schema=State)
workflow.add_node("plan", plan)
workflow.add_node("execute", execute)
workflow.add_node("check", check)

workflow.add_edge(LLMGraph.START, "plan")
workflow.add_edge("plan", "execute")
workflow.add_edge("execute", "check")
workflow.add_conditional_edge(
    "check", "decision",
    {"retry": "plan", "complete": LLMGraph.END},
)

print(workflow.get_graph_mermaid())   # Mermaid グラフを出力

gen = workflow.run({"input": "hello"})
try:
    while True:
        event = next(gen)
        print("event:", event)
except StopIteration as e:
    final_state = e.value
    print("final:", final_state)
```

## API

`from llm_graph_kit import LLMGraph, NodeState, NodeFunc` で公開シンボルを得られます。

### `LLMGraph(state_schema=None)`

グラフを作成する。

- `state_schema`: ステートのキーを宣言する `TypedDict`（または `__annotations__` を持つクラス）。新規コードでは必ず指定してください。
- 定数 `LLMGraph.START` / `LLMGraph.END` は擬似ノードを表す。
- 予約キー `__errors__` はライブラリが管理するためユーザーは書き込めない（後述）。

### `add_node(name, func)`

ノードを 1 つ登録する。`name` はユニークな文字列。`func` は次の「ノード関数」のいずれか。

### `add_edge(from_node, to_node)`

`from_node` から `to_node` への単一エッジを張る。
`from_node` に `LLMGraph.START` を渡すとエントリポイントが設定される。
`to_node` に `LLMGraph.END` を渡すと終端へ。

```python
g.add_edge(LLMGraph.START, "first_node")
g.add_edge("a", "b")
g.add_edge("last_node", LLMGraph.END)
```

### `add_conditional_edge(from_node, condition, path_map)`

`from_node` の出力に応じて遷移先を分岐する。

- `condition`:
  - **文字列**: `state[condition]` の値をシグナルに使う
  - **callable**: `state` を受け取り、文字列または `Enum` を返す関数
- `path_map`: シグナル値 → 次のノード名 の辞書。`LLMGraph.END` も値として使える
- `path_map` に該当キーがなければ自動で `END` へ遷移

```python
# (1) state のキーで分岐
g.add_conditional_edge("check", "decision",
                       {"retry": "plan", "complete": LLMGraph.END})

# (2) 関数で分岐
g.add_conditional_edge(
    "check",
    lambda s: "ok" if s["score"] >= 80 else "ng",
    {"ok": "publish", "ng": "revise"},
)

# (3) Enum で分岐（path_map のキーにもメンバーを置ける）
from enum import Enum
class Decision(Enum):
    RETRY = "retry"
    DONE = "done"

g.add_conditional_edge("check", "decision",
                       {Decision.RETRY: "plan", Decision.DONE: LLMGraph.END})
```

### `run(initial_state, max_steps=100)`

グラフを実行する。**ジェネレータ**を返す。

- `initial_state`: 初期 state。スキーマで宣言したキーのみ書き込み可
- `max_steps`: 実行できるノードの上限（既定 100）。サイクルで上限超過時は `RuntimeError`
- ノードから `yield` された任意の値はそのままジェネレータの出力に流れる
- すべての遷移が終わると最終 `state`（dict）が `StopIteration.value` として返る

```python
gen = workflow.run({"input": "x"})
try:
    while True:
        event = next(gen)
        # event を表示・配信する
except StopIteration as e:
    final_state = e.value
```

シンプルに全イベントを使い捨てるなら:

```python
for event in workflow.run({"input": "x"}):
    print(event)
```

### `get_graph_mermaid()`

グラフ構造を Mermaid 文字列で返す。コード/CLI からの貼り付けや LLM 出力での可視化向け。

```python
print(workflow.get_graph_mermaid())
```

## ノード関数の書き方

ノードは「state を受け取り、state 更新（dict）または `None` を返す関数」です。`yield` してジェネレータにすることで、実行中のイベントをストリーミングできます。

### 通常関数

```python
def my_node(state: NodeState):
    return {"key": "value"}   # ← state にマージされる
```

`None` を返してもよい（state 更新なし）。

### ジェネレータ（ストリーミング）

```python
def my_node(state: NodeState):
    yield {"type": "log", "content": "started"}
    yield {"type": "answer_text", "content": "chunk..."}
    return {"key": "value"}   # 最終 state 更新
```

`yield` した値はそのまま `run()` の出力に出ます。形式は任意で、ユーザーが好きに決められます（例: `{"type": "log", ...}` / `{"type": "answer_text", ...}` など）。

### ノード内例外

ノード内で例外が発生すると、ライブラリが自動で捕捉して以下を行います:

1. `state["__errors__"]` リストにエラーメッセージを追加（このキーはライブラリが管理）
2. `{"type": "error", "agent": <node_name>, "content": <message>}` を yield
3. グラフは止まらず、次の遷移ルールに進む（戻り値 None として扱う）

これにより、リトライループ等でエラーを引きずらないグラフが書けます。

## State の取り扱い

- 初期 state は内部で `deepcopy` され、呼び出し側のオブジェクトを変更しません
- 各ノードの戻り値 dict は `state.update(...)` で既存 state にマージされます（同名キーは上書き）
- `__errors__` は予約キー。`initial_state` でもノード戻り値でも書き込めません（読み取りは自由）

## サンプル: シンプルなカウンタ（LLM 非依存）

リポジトリの [`example_with_schema.py`](./example_with_schema.py) と同じものです。3 回ループしてから終端ノードに進むグラフです。

```python
"""
state_schema を使ったサンプル（LLM 非依存）。

TypedDict でステートのキーを宣言することで:
  - 実行時に未宣言キーへの書き込みを検出して即時に ValueError を発生
  - mypy / IDE で補完と型チェックが効く

実行:
    python example_with_schema.py
"""
from typing import TypedDict, Optional, List

from llm_graph_kit import LLMGraph, NodeState


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


if __name__ == "__main__":
    main()
```

実行例（このサンプルはノードが `yield` しないため、中間イベントはなく最終 state のみが得られます）:

```
--- final state ---
  input: hello
  __errors__: []
  count: 3
  history: ['started with input=hello', 'tick 1', 'tick 2', 'tick 3', 'done']
  decision: stop
  final_message: completed after 3 ticks
```

## LLM と組み合わせるときの推奨パターン

本ライブラリは特定の LLM ライブラリに依存しません。任意の LLM クライアントを**依存注入**でエージェントに渡し、ストリーミングチャンクを `yield` で呼び出し側へ流すのが推奨構成です。

```python
from typing import TypedDict
from llm_graph_kit import LLMGraph, NodeState


class QAState(TypedDict, total=False):
    question: str
    answer: str


class QAAgent:
    def __init__(self, llm) -> None:
        self.llm = llm            # LLM クライアントは外側で生成して注入する

    def run(self, question: str):
        graph = self.build_graph()
        yield from graph.run({"question": question})

    def build_graph(self) -> LLMGraph:
        g = LLMGraph(state_schema=QAState)
        g.add_node("answer", self._answer)
        g.add_edge(LLMGraph.START, "answer")
        g.add_edge("answer", LLMGraph.END)
        return g

    def _answer(self, state: NodeState):
        text = ""
        # お使いの LLM クライアントのストリーミング API に置き換えてください
        for chunk in self.llm.stream(state["question"]):
            text += chunk
            yield {"type": "answer_text", "node": "answer", "content": chunk}
        return {"answer": text}
```

このパターンの要点:

- **依存注入**: LLM は外側で生成してエージェントへ渡す。テスト時のモック化や LLM 実装の差し替えがしやすい
- **エージェントの境界**: グラフとノード関数をひとつのクラスに集約し、外側からは `agent.run(...)` だけで呼べる
- **`build_graph()` の分離**: グラフ構築を専用メソッドにすることで、テスト・可視化・サブグラフ化が容易
- **イベントの規約**: `type` で種別を分け（`log` / `answer_text` / `error` 等）、`node` キーに発火元を入れる

## GUI（ノードエディタ）

ブラウザ上でノードを繋いでエージェントを no-code で構築できる GUI を同梱しています。
FastAPI + Drawflow.js ベースで、ローカルで開発用に動かす想定です。

### インストール

```bash
uv add 'llm-graph-kit[gui]'
# または
pip install 'llm-graph-kit[gui]'
```

### 起動

```bash
# CLI から
python -m llm_graph_kit.gui
# ホスト/ポート指定
python -m llm_graph_kit.gui --host 127.0.0.1 --port 8000
```

または Python から:

```python
from llm_graph_kit import launch_gui
launch_gui(host="127.0.0.1", port=8000)
```

`http://127.0.0.1:8000/` をブラウザで開くと、以下の操作ができます。

- **+ Function / + Conditional**: 通常ノード・条件分岐ノードを追加
- ノードクリック → 右パネルで `name` / Python コード / condition / 出力 signal を編集
- ノード同士の出力ポート → 入力ポートをドラッグで **エッジを接続**
- 左パネルで **State Schema** (TypedDict) と **Initial State** (JSON) を編集
- **▶ Run**: グラフを実行し、`yield` されたイベントを下部にリアルタイム表示（SSE）
- **Mermaid**: 現在のグラフを Mermaid テキストで表示
- **Python Code**: 現在のグラフを実行可能な Python ソースとして出力
- **Save / Load**: グラフ仕様を JSON ファイルに保存・復元

### セキュリティ上の注意

GUI はユーザーが入力した Python コードを `exec` で評価します。
**信頼できないネットワークに公開してはいけません**。デフォルトの bind 先は
`127.0.0.1` で、外部からはアクセスできない設定になっています。

## ライセンス / リポジトリ
https://github.com/ToPo-ToPo-ToPo/llm-graph-kit

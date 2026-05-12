# llm-graph-kit

## 概要
LLMベースのエージェントの動作内容をグラフ形式で記述・実行するための軽量ライブラリです。
ノードとエッジでワークフローを定義し、逐次実行・条件分岐・Mermaid記法による可視化に対応しています。

## 主な機能
- **ノード/エッジによるワークフロー記述**: 各処理ステップをノードとして登録し、エッジで連結する
- **条件分岐**: `NodeState` の値や任意の関数で次のノードを動的に決定する
- **ストリーミング実行**: `run()` はジェネレータ。ノード関数からの `yield` イベント（ログ、途中経過など）を呼び出し側でそのまま受け取れる
- **Mermaid記法による可視化**: `get_graph_mermaid()` でグラフ構造を出力

## 環境構築
仮想環境を構築します。
```
uv venv
```
仮想環境を起動します。
```
source .venv/bin/activate
```
必要な依存ライブラリをインストールします。
```
uv sync
```

## クイックスタート
```python
from llm_graph_kit import LLMGraph

workflow = LLMGraph()

def plan(state):
    yield {"type": "log", "content": f"plan for: {state['input']}"}
    return {"plan": "draft a response"}

def execute(state):
    yield {"type": "log", "content": "executing..."}
    return {"output": f"done ({state['plan']})"}

def check(state):
    # state["decision"] を判定キーに使う
    return {"decision": "complete" if state.get("output") else "retry"}

workflow.add_node("plan", plan)
workflow.add_node("execute", execute)
workflow.add_node("check", check)

workflow.add_edge(LLMGraph.START, "plan")
workflow.add_edge("plan", "execute")
workflow.add_edge("execute", "check")

# decision の値で分岐
workflow.add_conditional_edge(
    "check",
    "decision",
    {"retry": "plan", "complete": LLMGraph.END},
)

# Mermaid 図を出力
print(workflow.get_graph_mermaid())

# 実行（ジェネレータ）
for event in workflow.run({"input": "hello"}):
    print(event)
```

## API リファレンス

### `LLMGraph`
グラフの本体。

| メソッド | 説明 |
|---|---|
| `add_node(name, func)` | ノードを登録する。`func` は `NodeState` を受け取る関数またはジェネレータ |
| `add_edge(from_node, to_node)` | 単一エッジを登録する。同じ `from_node` から2回呼ぶと `ValueError` |
| `add_conditional_edge(from_node, condition, path_map)` | 条件分岐を登録する。`condition` は `Callable[[NodeState], str]` または `NodeState` のキー名(`str`) |
| `run(initial_state)` | グラフを実行する。ジェネレータを返す |
| `get_graph_mermaid()` | グラフ構造を Mermaid 文字列で返す |

### 定数
- `LLMGraph.START`: エントリポイントを示す擬似ノード
- `LLMGraph.END`: 終了を示す擬似ノード

## ノード関数の仕様
ノード関数は以下のいずれかを返せます。

- **通常関数**: `dict`（または `None`）を返す → `state` に `update()` でマージされる
- **ジェネレータ**: `yield` した値は `run()` から呼び出し側へそのまま流れる（ストリーミング用途）。`return` 値があれば `state` にマージされる

```python
def my_node(state):
    yield {"type": "log", "content": "..."}  # 呼び出し側へストリーミング
    return {"key": "value"}                   # state にマージ
```

## 条件分岐
`add_conditional_edge` の `condition` には2通り渡せます。

```python
# (1) 関数を渡す
workflow.add_conditional_edge(
    "check",
    lambda state: "ok" if state["score"] > 80 else "ng",
    {"ok": "next_node", "ng": "retry_node"},
)

# (2) NodeState のキー名を渡す（その値を判定に使う）
workflow.add_conditional_edge(
    "check",
    "decision",
    {"retry": "create_draft", "complete": "finalize"},
)
```

`path_map` のキーに該当する遷移先がない場合は `LLMGraph.END` へ遷移します。

## 予約キー
`run()` はワークフロー実行時に `state["__errors__"]` を初期化します（リスト）。
ノード関数内で例外が発生すると、エラー情報がここに蓄積され、
さらに `{"type": "error", "agent": <node>, "content": <message>}` イベントが yield されます。

## ライセンス / リポジトリ
https://github.com/ToPo-ToPo-ToPo/llm_graph

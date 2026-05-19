# llm-graph-kit

## 概要
LLM ベースのエージェントの動作内容をグラフ形式で記述・実行するための軽量ライブラリです。
ノードとエッジでワークフローを定義し、逐次実行・条件分岐・Mermaid 記法による可視化に対応しています。

主な特徴:
- **ノード/エッジ**でワークフローを宣言的に記述
- **TypedDict ベースのスキーマ**でステートのキーを宣言し、実行時の型ミスや誤キー書き込みを早期検出
- **ストリーミング実行**: ノードから `yield` したイベントが呼び出し側にそのまま流れる
- **構築時バリデーション**: ノード重複登録、エッジ競合、未登録ノード参照を即座に検出
- **実行時の安全装置**: `max_steps` による無限ループ防止、戻り値型チェック、予約キー保護
- **Mermaid 出力**でグラフ構造を可視化

---

## インストール

仮想環境を作って依存をインストールします。

```bash
uv venv
source .venv/bin/activate
uv sync
```

ライブラリとして利用するだけなら:

```bash
uv add llm-graph-kit
```

---

## クイックスタート

### 推奨: `state_schema` でステートを宣言する

ステートのキーを `TypedDict` で宣言しておくと、未宣言キーへの書き込みやタイポを実行時に即座に検出できます。

```python
from typing import TypedDict, Optional, List
from llm_graph_kit import LLMGraph, NodeState


class WorkflowState(TypedDict, total=False):
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


workflow = LLMGraph(state_schema=WorkflowState)

workflow.add_node("plan", plan)
workflow.add_node("execute", execute)
workflow.add_node("check", check)

workflow.add_edge(LLMGraph.START, "plan")
workflow.add_edge("plan", "execute")
workflow.add_edge("execute", "check")
workflow.add_conditional_edge(
    "check",
    "decision",
    {"retry": "plan", "complete": LLMGraph.END},
)

# Mermaid 図を出力
print(workflow.get_graph_mermaid())

# 実行（ジェネレータ）
gen = workflow.run({"input": "hello"})
final_state = None
try:
    while True:
        event = next(gen)
        print("event:", event)
except StopIteration as e:
    final_state = e.value

print("final:", final_state)
```

### スキーマなし（非推奨、後方互換用）

```python
workflow = LLMGraph()  # DeprecationWarning が出る
```

`state_schema` を渡さずに作ると **`DeprecationWarning`** が発出されます。将来のバージョンでスキーマ必須化を予定しているため、新規コードでは必ずスキーマを宣言してください。

---

## API リファレンス

### `class LLMGraph(state_schema: Optional[Type] = None)`

| パラメータ | 説明 |
|---|---|
| `state_schema` | ステートのキーを宣言する `TypedDict`（または `__annotations__` を持つクラス）。指定すると、未宣言キーへの書き込み・タイポ・予約キー衝突を実行時に `ValueError` で検出する。`None` の場合は `DeprecationWarning` が出る。 |

#### 定数
- `LLMGraph.START`: エントリポイントを示す擬似ノード（値は `"__START__"`）
- `LLMGraph.END`: 終了を示す擬似ノード（値は `"__END__"`）
- `LLMGraph.RESERVED_STATE_KEYS`: ライブラリ管理の予約キー集合。現在は `{"__errors__"}`

#### メソッド

##### `add_node(name: str, func: NodeFunc) -> None`
ノードを登録する。

| 検出 | 条件 |
|---|---|
| `ValueError` | `name` が `START` / `END` と同じ |
| `ValueError` | `name` が既に登録済み（重複登録の防止） |

##### `add_edge(from_node: str, to_node: str) -> None`
単一エッジを登録する。

- `from_node` が `START` の場合、`to_node` がエントリポイントとして設定される
- 同じ `from_node` から再度 regular edge を張ると `ValueError`
- 既に conditional edge が張られているノードに regular edge を張ると `ValueError`
- 既にエントリポイントが設定済みの状態で `START` から再度張ろうとすると `ValueError`
- `from_node == END` は `ValueError`

##### `add_conditional_edge(from_node: str, condition: Union[Callable[[NodeState], str], str], path_map: Dict[str, str]) -> None`
条件分岐を登録する。

- `condition` には次のいずれかを渡す:
  - **callable**: `state` を受け取り、ルーティングシグナル（`str` / `Enum`）を返す関数
  - **str**: `state[condition]` の値をシグナルとして使う
- `condition` が文字列の場合、`state_schema` 宣言キーに含まれないと `ValueError`
- `path_map` のキーは Enum でも可（`Enum.name` で照合される）
- 同じ `from_node` への重複登録、または regular edge との混在は `ValueError`

##### `run(initial_state: NodeState, max_steps: int = 100) -> Generator[Any, None, NodeState]`
グラフを実行する。ジェネレータを返す。

| パラメータ | 説明 |
|---|---|
| `initial_state` | 初期ステート。`state_schema` 宣言時は未宣言キーがあれば `ValueError`。予約キー（`__errors__`）の事前注入も `ValueError` |
| `max_steps` | 実行できるノード数の上限（デフォルト 100）。サイクルで超過すると `RuntimeError`。正の整数のみ |

実行の流れ:

1. `entry_point` が設定済みか確認 → 無ければ `ValueError`
2. `max_steps` を検証
3. **`_validate_graph()` でグラフ構造を一括検証**（未登録ノード参照は実行前に検出）
4. `initial_state` のキーを検証
5. ステートを `deepcopy` し、`state["__errors__"] = []` を内部で初期化
6. エントリポイントからノードを順次実行
7. ノード戻り値が dict 以外なら `TypeError`、宣言外キーなら `ValueError`
8. END に到達すると最終 state を `return` する（`StopIteration.value` で取得）

##### `get_graph_mermaid() -> str`
グラフ構造を Mermaid 文字列で返す。ノードラベルは二重引用符で囲まれ、特殊文字を含む名前でも構文が壊れない。

### 型エイリアス

```python
NodeState = Dict[str, Any]

NodeFunc = Callable[
    [NodeState],
    Union[
        Optional[NodeState],
        Generator[Any, None, Optional[NodeState]],
    ],
]
```

---

## ノード関数の仕様

ノード関数は以下のいずれかの形で実装できます。

### 1. 通常関数（dict または None を返す）

```python
def my_node(state: NodeState):
    return {"key": "value"}      # state にマージされる
```

`None` 戻りも許容され、その場合はステート更新なし。

### 2. ジェネレータ（イベントを `yield` しながら最後に `return`）

```python
def my_node(state: NodeState):
    yield {"type": "log", "content": "started"}      # 呼び出し側へストリーミング
    yield {"type": "answer_text", "content": "..."}  # 任意の中間イベント
    return {"key": "value"}                          # 最終 state 更新
```

`yield` した値はそのまま `run()` の出力に流れます。
`return` した dict が `state.update()` に渡されます。

### 戻り値の型チェック

dict・None 以外を返すと **即座に `TypeError`**:

```
TypeError: Node 'foo' must return a dict or None, got str.
```

うっかり文字列を直接 return してしまった場合のデバッグが容易です。

### 例外処理

ノード内で例外が発生した場合、グラフは停止せず:

1. `state["__errors__"]` にエラー文字列を追加
2. `{"type": "error", "agent": <node_name>, "content": <message>}` を yield
3. 次の遷移判定へ進む（戻り値 None として扱う）

これにより、リトライループなどでエラーをハンドリングしながら継続できます。

---

## 条件分岐

`add_conditional_edge` の `condition` には 2 通りを渡せます。

### (1) 関数を渡す

```python
workflow.add_conditional_edge(
    "check",
    lambda state: "ok" if state["score"] >= 80 else "ng",
    {"ok": "next_node", "ng": "retry_node"},
)
```

### (2) `NodeState` のキー名（文字列）を渡す

```python
workflow.add_conditional_edge(
    "check",
    "decision",                              # state["decision"] を判定に使う
    {"retry": "create_draft", "complete": "finalize"},
)
```

スキーマ宣言時、キー名が未宣言なら構築時に `ValueError`。

### Enum を使ったルーティング

`Enum` メンバーは `.name` で照合されます。state 側にも `path_map` 側にも置けます。

```python
from enum import Enum

class Decision(Enum):
    RETRY = "retry"
    COMPLETE = "complete"

def check(state):
    return {"decision": Decision.RETRY}

workflow.add_conditional_edge(
    "check",
    "decision",
    {"RETRY": "plan", "COMPLETE": LLMGraph.END},   # Enum.name で照合
)
# あるいは path_map のキーに Enum を直接渡しても OK
workflow.add_conditional_edge(
    "check",
    "decision",
    {Decision.RETRY: "plan", Decision.COMPLETE: LLMGraph.END},
)
```

`path_map` に該当キーがなければ自動的に `LLMGraph.END` へ遷移します。

---

## 予約キーとエラー処理

### `__errors__`
ライブラリが管理する予約キー。`run()` 開始時に空リストとして初期化され、ノード実行で例外が出るたびに以下が追加されます。

```
"Error in <node_name>: <message>"
```

ユーザーは `__errors__` を **`initial_state` でもノード戻り値でも書き込めません**（即時 `ValueError`）。誤って上書きしてエラー履歴が失われることを防ぎます。

### エラーイベント
ノード実行中の例外は捕捉され、以下の形でストリームに流れます:

```python
{"type": "error", "agent": "<node_name>", "content": "<message>"}
```

ワークフロー自体は停止せず、次の遷移判定に進むため、リトライ系のグラフを書きやすくなっています。

---

## 安全機構

### 構築時バリデーション（即時 `ValueError`）

| 検出ケース | 例 |
|---|---|
| 予約ノード名 (`START` / `END`) を `add_node` | `add_node("__START__", f)` |
| 同名ノードの重複登録 | `add_node("a", f1); add_node("a", f2)` |
| `START` からの二重エッジ | `add_edge(START, "a"); add_edge(START, "b")` |
| 同じ `from_node` からの regular edge 重複 | `add_edge("a", "b"); add_edge("a", "c")` |
| 同じ `from_node` からの conditional edge 重複 | 同上の conditional 版 |
| 同じノードに regular + conditional 混在 | `add_edge("a", "b"); add_conditional_edge("a", ...)` |
| スキーマ宣言時に未宣言キーを condition に指定 | `add_conditional_edge("a", "typo", {...})` |

### 実行前の整合性検証
`run()` の冒頭で全ての `entry_point` / edge / path_map の遷移先がノード集合（または END）に含まれるかを一括検証します。タイポは **1 ノードも実行する前に** ValueError として捕捉されます。

### 実行時の安全装置

| 機構 | 動作 |
|---|---|
| `max_steps` | デフォルト 100 ノード実行を上限とし、超過したら `RuntimeError`。サイクル付きグラフの暴走を防ぐ |
| スキーマ違反 | ノード戻り値に未宣言キーが含まれると即時 `ValueError` |
| 戻り値型違反 | ノードが dict・None 以外を返すと `TypeError` |
| 予約キー保護 | `initial_state` / ノード戻り値が `__errors__` を含むと `ValueError` |
| 入力の不変性 | `initial_state` は `deepcopy` され、呼び出し側のオブジェクトを変更しない |

---

## Mermaid 可視化

```python
print(workflow.get_graph_mermaid())
```

出力例:

```
graph TD
  classDef startClass fill:#f9f,stroke:#333,stroke-width:2px;
  classDef endClass fill:#f96,stroke:#333,stroke-width:2px;
  classDef nodeClass fill:#e1f5fe,stroke:#0277bd,stroke-width:2px;
  classDef routerClass fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;
  __START__(START):::startClass
  __END__(END):::endClass
  plan["plan"]:::nodeClass
  execute["execute"]:::nodeClass
  check{"check"}:::routerClass
  __START__ --> plan
  plan --> execute
  execute --> check
  check -- retry --> plan
  check -- complete --> __END__
```

ノード名は二重引用符でラップされるため、特殊文字を含む名前でも壊れません。

---

## 完全な例

`example_with_schema.py` に LLM 非依存の動作可能なサンプルがあります（カウンタを 3 回まわして終了するグラフ + 各種バリデーションのデモ）。

```bash
python example_with_schema.py
```

LLM を使う実例として `test_novelist.py` も同梱しています。

---

## 非推奨・将来の変更予定

- `LLMGraph(state_schema=None)` は将来のバージョンで削除予定。現在は `DeprecationWarning` のみ。

---

## ライセンス / リポジトリ
https://github.com/ToPo-ToPo-ToPo/llm_graph

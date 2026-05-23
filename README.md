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

開発する場合:

```bash
uv venv
source .venv/bin/activate
uv sync
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

## サンプル 1: シンプルなカウンタ（LLM 非依存）

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

## サンプル 2: LLM を使うエージェント

リポジトリの [`example_with_llm.py`](./example_with_llm.py) と同じものです。
「質問に答える → 答えをチェック → 短ければやり直す」という 2 段のグラフを構築します。LLM のストリーミングチャンクを `yield` でそのまま呼び出し側へ流す、エージェント実装の典型構成です。

**構成**:

- LLM は外側で生成してエージェントへ注入する(依存注入)
- グラフ構築を `build_graph()` メソッドに分離(再利用・テストしやすい)
- 各ノードはエージェントの private メソッド (`_answer`, `_check` など)
- 公開エントリポイント `run()` は initial state の組み立て、Mermaid 図の通知、下位グラフからのイベント中継 (`yield from`) を担当

```python
"""
LLM を使った最小サンプル(エージェント)。

質問に答えるノードと、答えをチェックするノードからなる 2 段グラフ。
チェックで「短すぎる」と判定されたらやり直す。最大 3 回でループを抜ける。

実行:
    python example_with_llm.py
"""
from typing import TypedDict

from augllm import MlxLLM

from llm_graph_kit import LLMGraph, NodeState


# ---------------------------------------------------------------------------
# ステートのスキーマ
# ---------------------------------------------------------------------------
class QAState(TypedDict, total=False):
    question: str
    answer: str
    attempts: int
    decision: str


# ---------------------------------------------------------------------------
# エージェントクラス
# ---------------------------------------------------------------------------
class QAAgent:
    """質問応答エージェント。LLM を保持し、グラフの定義と実行を提供する。"""

    # --------------------------------------------------
    # 初期化
    # --------------------------------------------------
    def __init__(self, llm) -> None:
        self.llm = llm

    # --------------------------------------------------
    # 公開エントリポイント
    # --------------------------------------------------
    def run(self, question: str):
        """質問を受け取り、グラフ実行中のイベントを呼び出し側へ yield する。"""
        graph = self.build_graph()

        # まずグラフ構造を log イベントとして流しておく(呼び出し側で可視化できる)
        yield {
            "type": "log",
            "node": "system",
            "content": f"Workflow Definition:\n{graph.get_graph_mermaid()}",
        }

        # 下位グラフのイベントをそのまま中継
        initial_state = {"question": question, "attempts": 0}
        yield from graph.run(initial_state)

    # --------------------------------------------------
    # グラフ定義
    # --------------------------------------------------
    def build_graph(self) -> LLMGraph:
        """このエージェントのワークフローを構築して返す。"""
        g = LLMGraph(state_schema=QAState)

        # ノード登録
        g.add_node(name="answer", func=self._answer)
        g.add_node(name="check", func=self._check)

        # エッジ定義
        g.add_edge(LLMGraph.START, "answer")
        g.add_edge("answer", "check")

        # 条件付きエッジ: state["decision"] によって遷移先を切り替える
        g.add_conditional_edge(
            "check",
            "decision",
            {
                "retry": "answer",
                "ok": LLMGraph.END,
            },
        )
        return g

    # --------------------------------------------------
    # ノード: LLM で回答を生成(ストリーミング)
    # --------------------------------------------------
    def _answer(self, state: NodeState):
        node_name = "answer"
        yield {"type": "log", "node": node_name, "content": "answering..."}

        system_prompt = "あなたは簡潔に答えるアシスタントです。"
        user_prompt = f"質問: {state['question']}"

        # LLM のチャンクをそのまま呼び出し側へ流す
        text = ""
        for chunk in self.llm.respond(
            system_prompt=system_prompt, user_text=user_prompt, stream=True
        ):
            text += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "taskId": f"{node_name}-answer-text",
                "content": chunk,
            }

        # state を更新(回答本文と試行回数のインクリメント)
        return {"answer": text, "attempts": state.get("attempts", 0) + 1}

    # --------------------------------------------------
    # ノード: 回答の品質チェックと分岐シグナルの決定
    # --------------------------------------------------
    def _check(self, state: NodeState):
        node_name = "check"

        too_short = len(state["answer"]) < 30
        give_up = state["attempts"] >= 3
        decision = "retry" if (too_short and not give_up) else "ok"

        yield {
            "type": "log",
            "node": node_name,
            "content": (
                f"len={len(state['answer'])} attempts={state['attempts']}"
                f" -> {decision}"
            ),
        }
        return {"decision": decision}


# ---------------------------------------------------------------------------
# メインプログラム
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 1. エージェントを構築
    LLM_PATH = "mlx-community/Qwen3.6-27B-4bit"
    llm = MlxLLM(model_path=LLM_PATH)
    agent = QAAgent(llm=llm)

    # 2. 入力
    question = "地球の半径は何 km?"
    print(f"Request: {question}\n")

    # 3. 実行(ストリーミングを受け取り、イベント種別ごとに表示先を変える)
    for event in agent.run(question=question):

        # LLM 出力のチャンク
        if event["type"] == "answer_text":
            print(event["content"], end="", flush=True)

        # ログ
        elif event["type"] == "log":
            print(f"\n[LOG] {event['node']}: {event['content']}")

        # ノード内例外(ライブラリが自動で yield する)
        elif event["type"] == "error":
            print(f"\n[ERROR] {event['agent']}: {event['content']}")

    print("\n\nProcess Completed.")
```

このサンプルで示しているパターン:

- **依存注入**: LLM は外側で生成してエージェントへ渡す。テスト時のモック化や LLM 実装の差し替えがしやすい
- **エージェントの境界**: グラフとノード関数をひとつのクラスに集約し、外側からは `agent.run(...)` だけで呼べる
- **`build_graph()` の分離**: グラフ構築を専用メソッドにすることで、テスト・可視化・サブグラフ化が容易
- **`run()` の責務**: initial state の生成と `yield from graph.run(...)` による中継だけに留め、各ノードの処理は private メソッドに任せる
- **イベントの規約**: `type` で種別を分け(`log` / `answer_text` / `error` 等)、`node` キーに発火元を入れる
- **`__errors__` は触らない**: ノードで例外が起きるとライブラリが自動で `{"type": "error", "agent": ..., "content": ...}` を流す
- **リトライの上限はドメイン側**: `attempts >= 3` で `decision="ok"` にして抜ける。`max_steps` はライブラリ側のセーフティネット

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
https://github.com/ToPo-ToPo-ToPo/llm_graph

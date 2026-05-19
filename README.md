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

リポジトリの [`example_with_schema.py`](./example_with_schema.py) と同じものです。3 回ループしてから終端ノードに進むグラフと、スキーマ違反の挙動デモを含みます。

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

実行例:

```
event: {'count': 0, 'history': ['started with input=hello']}      # ← yieldしていないので実際は出ない
--- final state ---
  input: hello
  __errors__: []
  count: 3
  history: ['started with input=hello', 'tick 1', 'tick 2', 'tick 3', 'done']
  decision: stop
  final_message: completed after 3 ticks
```

（上記サンプルはノードが `yield` しないため、`run()` から流れる中間イベントはなく、最終 state のみが取得されます。）

## サンプル 2: LLM を使った小説執筆エージェント

リポジトリの [`test_novelist.py`](./test_novelist.py) と同じものです。
`augllm.MlxLLM` で LLM を呼び出し、原稿作成 → レビュー → 判定 → リトライまたは最終出力、という分岐付きのグラフを構築します。LLM ストリーミングのチャンクを `yield` してそのまま CLI に流す典型例です。

```python
# 依存ライブラリのインポート
from augllm import MlxLLM
import yaml

# 自作ライブラリのインポート
from llm_graph_kit import LLMGraph, NodeState

#-----------------------------------------------------------------------
# エージェントクラス
#-----------------------------------------------------------------------
class NovelistAgent:

    #--------------------------------------------------
    # 初期化
    #--------------------------------------------------
    def __init__(self, model_path: str) -> None:
        # LLMの初期化
        print(f"Loading Model: {model_path} ...")
        self.llm = MlxLLM(model_path=model_path)

    #---------------------------------------------------------------------------
    # エージェントを実行 (メインエントリポイント)
    #---------------------------------------------------------------------------
    def run(self, question: str):
        """
        ジェネレータを返すように変更。
        呼び出し元でforループを回して結果を取得する。
        """

        # 1. メイングラフの構築
        main_workflow = self.create_main_graph()

        # 2. グラフ構造の可視化 (Mermaid) - イベントとして送信
        yield {
            "type": "log",
            "node": "system",
            "content": f"Workflow Definition:\n{main_workflow.get_graph_mermaid()}"
        }

        # 3. ワークフローの実行
        initial_input = {"input": question}

        # 【修正箇所】return ではなく yield from を使用して、下位ジェネレータの値を流す
        yield from main_workflow.run(initial_input)

    #---------------------------------------------------------------------------
    # メイングラフの定義
    #---------------------------------------------------------------------------
    def create_main_graph(self) -> LLMGraph:
        """
        メインのワークフローを定義します。
        """
        workflow = LLMGraph()

        # ノード登録
        workflow.add_node(name="create_manuscript", func=self._create_manuscript)
        workflow.add_node(name="review", func=self._create_review)
        workflow.add_node(name="check_result", func=self._check_result_node)
        workflow.add_node(name="create_book_blurb", func=self._create_book_blurb)

        # エッジ定義
        workflow.add_edge(LLMGraph.START, "create_manuscript")
        workflow.add_edge("create_manuscript", "review")
        workflow.add_edge("review", "check_result")

        # 条件付きエッジ
        workflow.add_conditional_edge(
            "check_result",
            "decision",
            {
                "retry": "create_manuscript",
                "complete": "create_book_blurb"
            }
        )

        workflow.add_edge("create_book_blurb", LLMGraph.END)

        return workflow

    #---------------------------------------------------------------------------
    # LLMで回答のドラフトを作成
    #---------------------------------------------------------------------------
    def _create_manuscript(self, state: NodeState):
        node_name = "create_manuscript"

        yield {"type": "log", "node": node_name, "content": "Create draft node start"}

        input_text = state["input"]
        review_result = state.get("review_report", "")
        feedback = state.get("review_advice", "")
        pre_manuscript = state.get("manuscript", "")

        system_prompt = (
            "あなたは、短編小説の「あらすじ」を作成する小説家です。\n"
            "あなたの仕事は、与えられた指示をもとに、小説の「あらすじ」を考えることです。"
        )

        user_prompt = (
            "以下の指示をもとに小説の「あらすじ」を考えてください。\n\n"
            "### 与えられた指示\n"
            f"{input_text}"
        )

        if pre_manuscript:
            user_prompt = (
                "前回作成したあらすじに対して、編集者から厳しいレビューが入りました。\n"
                "指摘事項と評価スコアを反映し、**劇的に改善された修正版**を作成してください。\n\n"
                "### 元の指示の内容\n"
                f"{input_text}\n\n"
                "### 前回作成した「あらすじ」\n"
                f"{pre_manuscript}\n\n"
                f"{review_result}\n\n"
                "### 編集者からの総評・修正指示\n"
                f"{str(feedback)}"
            )

        # LLMで回答を作成 (ストリーミング)
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "taskId": f"{node_name}-answer-text",
                "content": chunk
            }

        yield {"type": "log", "node": node_name, "content": "原稿作成完了"}

        return {"manuscript": response}

    #---------------------------------------------------------------------------
    # LLMでレビューを実施(判定ロジックをPython側に移譲)
    #---------------------------------------------------------------------------
    def _create_review(self, state: NodeState):
        node_name = "review"

        yield {"type": "log", "node": node_name, "content": "Create review node start"}

        input_text = state["input"]
        manuscript = state["manuscript"]
        retry_count = state.get("retry_count", 0)

        # 無限ループの回避用
        if retry_count >= 5:
            yield {"type": "log", "node": node_name,
                   "content": f"Max retries ({retry_count}) reached. Approving result."}
            return {
                "review_judgement": "OK",
                "review_advice": [],
                "review_report": "最大リトライ回数に達したため、現在の原稿を採用します。"
            }

        system_prompt = (
            "あなたは、作家の卵を育てる**建設的で親切な編集者**です。\n"
            "「あらすじ」の完成度を高めるために、良い点は褒め、改善点は具体的にアドバイスしてください。\n\n"
            "以下の評価項目について評価を行ってください。\n"
            "1. 読者を惹きつける内容か? (attractiveness)\n"
            "2. 内容はわかりやすいか? (clarity)\n"
            "3. 指示への適合性 (instruction_alignment)\n"
            "4. 公開安全性 (public_safety)\n\n"
            "### 出力形式(厳守)\n"
            "必ず以下の YAML 形式のみで出力してください。\n\n"
            "scores:\n"
            "  attractiveness:\n"
            "    score: <0-10の整数>\n"
            "    reason:\n"
            "      - \"<評価理由>\"\n"
            "  clarity:\n"
            "    score: <0-10の整数>\n"
            "    reason:\n"
            "      - \"<評価理由>\"\n"
            "  instruction_alignment:\n"
            "    score: <0-10の整数>\n"
            "    reason:\n"
            "      - \"<評価理由>\"\n"
            "  public_safety:\n"
            "    score: <0-10の整数>\n"
            "    reason:\n"
            "      - \"<評価理由>\"\n"
            "advice:\n"
            "  - \"<点数が低い項目の改善指示1>\"\n"
            "  - \"<特になければ空配列 [] のみとすること>\""
        )

        user_prompt = (
            "以下の小説の「あらすじ」について、編集者として厳しくレビューをしてください。\n\n"
            "### 与えられた指示\n"
            f"{input_text}\n\n"
            "### 現状の小説の「あらすじ」\n"
            f"{manuscript}"
        )

        # LLMで回答を作成
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "taskId": f"{node_name}-answer-text",
                "content": chunk
            }

        yield {"type": "log", "node": node_name, "content": "レビュー生成完了。解析を開始します。"}

        # レビュー結果のパース(YAMLとしてパース)
        try:
            cleaned_response = response.strip()
            if "```yaml" in cleaned_response:
                cleaned_response = cleaned_response.split("```yaml")[1].split("```")[0].strip()
            elif "```" in cleaned_response:
                cleaned_response = cleaned_response.split("```")[1].split("```")[0].strip()
            review_data = yaml.safe_load(cleaned_response)
        except Exception as e:
            yield {"type": "log", "node": node_name, "content": f"[Error] YAML Parse failed: {e}"}
            review_data = {"scores": {}, "advice": ["レビュー解析エラー"]}

        # 機械的な判定ロジック (全項目が pass_threshold 以上なら OK)
        scores = review_data.get("scores", {})
        formatted_report = ""
        final_judgement = "OK"
        pass_threshold = 7

        if scores:
            report_lines = ["### 評価レポート"]
            for category, data in scores.items():
                s_val = int(data.get('score', 0))
                reasons = data.get('reason', [])
                if s_val < pass_threshold:
                    final_judgement = "NG"
                report_lines.append(f"- **{category}**: {s_val}/10点")
                if isinstance(reasons, list):
                    for r in reasons:
                        report_lines.append(f"  - {r}")
                else:
                    report_lines.append(f"  - {reasons}")
            formatted_report = "\n".join(report_lines)
        else:
            final_judgement = "NG"
            formatted_report = "評価データの取得に失敗しました。"

        # アドバイスの整形
        advice_list = review_data.get("advice", [])
        advice_text = "\n".join([f"- {item}" for item in advice_list]) if advice_list else "(特になし)"

        yield {
            "type": "log",
            "node": node_name,
            "content": f"機械判定結果: {final_judgement} (閾値: {pass_threshold}点)\n{formatted_report}"
        }

        return {
            "review_judgement": final_judgement,
            "review_report": formatted_report,
            "review_advice": advice_text,
            "retry_count": retry_count + 1
        }

    #---------------------------------------------------------------------------
    # チェックノード
    #---------------------------------------------------------------------------
    def _check_result_node(self, state: NodeState):
        """
        レビュー結果を確認し、次のアクションを決定するノード。
        """
        node_name = "check_result"
        review_judgement = state.get("review_judgement", "NG")
        decision = "complete" if review_judgement == "OK" else "retry"

        yield {
            "type": "log",
            "node": node_name,
            "content": f"[Check Node] Judgement: {review_judgement} -> Decision: {decision}"
        }

        return {"decision": decision}

    #---------------------------------------------------------------------------
    # 承認済みプロットから「本の裏表紙(Blurb)」を作成するノード
    #---------------------------------------------------------------------------
    def _create_book_blurb(self, state: NodeState):
        node_name = "create_book_blurb"
        yield {"type": "log", "node": node_name, "content": "Create Book Blurb Start"}

        approved_plot = state["manuscript"]
        feedback = state.get("review_advice", "")

        system_prompt = (
            "あなたは純文学の小説家です。\n"
            "あなたの仕事は、渡されたプロットを元に、**小説のあらすじ**を執筆することです。\n"
            "### ルール(厳守)\n"
            "- 出力はあらすじ部分のみとしてください。"
        )

        user_prompt = (
            "以下のプロットを元に、魅力的な小説のあらすじを書いてください。\n\n"
            "### 物語のプロット(全容)\n"
            f"{approved_plot}\n\n"
            "### 編集者からの執筆アドバイス\n"
            f"{feedback}"
        )

        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "taskId": f"{node_name}-answer-text",
                "content": chunk
            }

        yield {"type": "log", "node": node_name, "content": "Blurb作成完了"}
        return {"final_novel": response}


#-----------------------------------------------------------------------
# メインプログラム
#-----------------------------------------------------------------------
if __name__ == "__main__":
    # 1. エージェントの構築
    LLM_PATH = "mlx-community/Qwen3.6-27B-4bit"
    agent = NovelistAgent(model_path=LLM_PATH)

    # 2. 入力
    question = "伝説の野球選手というテーマで小説のあらすじを作成してください。"
    print(f"Request: {question}\n")

    # 3. 実行 (ストリーミングの受け取り)
    for event in agent.run(question=question):
        if event["type"] == "answer_text":
            print(event["content"], end="", flush=True)
        elif event["type"] == "log":
            print(f"\n[LOG] {event['node']}: {event['content']}")
        elif event["type"] == "images":
            print(f"\n[IMAGE] {event['content']}")

    print("\n\nProcess Completed.")
```

このサンプルでは:

- `_create_manuscript` / `_create_review` / `_create_book_blurb` はジェネレータノードで、LLM のチャンクを `yield` してそのまま CLI へ流している
- `_check_result_node` で `state["decision"]` を `"retry" | "complete"` に設定
- `add_conditional_edge("check_result", "decision", {...})` でリトライループを構成
- `retry_count >= 5` はドメイン側のリトライ上限（ライブラリ側の暴走対策とは独立）
- イベント `{"type": "log" | "answer_text" | "images"}` のスキーマは利用側の規約に過ぎず、ライブラリはイベントの中身を解釈しない

## ライセンス / リポジトリ
https://github.com/ToPo-ToPo-ToPo/llm_graph

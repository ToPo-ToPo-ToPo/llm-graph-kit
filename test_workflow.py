
# 依存ライブラリのインポート
from mlx_augllm import MlxLLM
import json
import re

# 自作ライブラリのインポート
from llm_graph.llm_graph import LLMGraph, State
from llm_graph.graph_logger import GraphLogger

#-----------------------------------------------------------------------
# エージェントクラス
#-----------------------------------------------------------------------
class Agent:

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
    def run(self, question: str) -> State:
        """
        質問を受け取り、グラフを構築・可視化・実行して結果を返します。
        """

        # 1. メイングラフの構築(この時点で内部でサブグラフも定義される)
        main_workflow = self.create_main_graph()

        # 2. グラフ構造の可視化 (Mermaid)
        print("\n" + "="*60)
        print(" Workflow Visualization (Mermaid)")
        print("="*60)
        print(main_workflow.get_graph_mermaid())
        print("="*60 + "\n")

        # 3. ワークフローの実行
        initial_input = {"input": question}
        final_state = main_workflow.run(initial_input)
        
        return final_state
    
    #---------------------------------------------------------------------------
    # メイングラフの定義
    #---------------------------------------------------------------------------
    def create_main_graph(self) -> LLMGraph:
        """
        メインのワークフローを定義します。
        """

        #------------------------------------------------------------
        # 初期化
        #------------------------------------------------------------
        workflow = LLMGraph()

        #------------------------------------------------------------
        # ノード登録
        #------------------------------------------------------------
        workflow.add_node(name="create_manuscript", func=self._create_manuscript)
        workflow.add_node(name="review", func=self._create_review)

        #------------------------------------------------------------
        # エッジ定義
        #------------------------------------------------------------
        workflow.add_edge(LLMGraph.START, "create_manuscript")
        workflow.add_edge("create_manuscript", "review")
        # 条件付きエッジ
        workflow.add_conditional_edge(
            "review",         # 分岐元
            self._loop_check, # ルーター関数
            {
                "retry": "create_manuscript",  # NGなら最初に戻る
                "complete": LLMGraph.END       # OKなら終了
            }
        )

        return workflow
    
    #---------------------------------------------------------------------------
    # LLMで回答のドラフトを作成
    #---------------------------------------------------------------------------
    def _create_manuscript(self, state: State) -> State:

        # ログの作成
        GraphLogger.print_phase_header("Create draft node", emoji="🟠")

        # 必要なデータを取得
        input = state["input"]
        feedback = state.get("review_advice", "")

        # システムプロンプトの構築
        system_prompt = (
            "あなたは、短編小説の「あらすじ」を作成する小説家です。\n"
            "あなたの仕事は、与えられた指示をもとに、小説の「あらすじ」を考えることです。"
        )

        # ユーザープロンプトの構築
        user_prompt = (
            "以下の指示をもとに小説の「あらすじ」を考えてください。\n\n"
            "### 与えられた指示\n"
            f"{input}"
        )
        
        # フィードバックがある場合
        if feedback:
            user_prompt = (
                "レビューの内容をもとに、小説の「あらすじ」の修正版を作成してください。\n\n"
                "### 元の指示の内容\n"
                f"{input}\n"
                "\n"
                "### レビュー内容\n"
                f"{feedback}"
            )

        # LLMで回答を作成
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        # 結果の出力
        GraphLogger.log(title="原稿作成結果:", content=response, style="response")

        # 次のノードへの引き継ぎ情報
        return {"manuscript": response}
    
    #---------------------------------------------------------------------------
    # LLMでレビューを実施
    #---------------------------------------------------------------------------
    def _create_review(self, state: State) -> State:

        # 実行中の表示
        GraphLogger.print_phase_header("Create review node", emoji="🟠")

        # 必要なデータを取得
        input = state["input"]                     # 元のお題
        manuscript = state["manuscript"]           # 作成された原稿
        retry_count = state.get("retry_count", 0)  # リトライの回数

        # 無限ループの回避用
        if retry_count >= 2:
            print(f"[Sub: Eval] Max retries ({retry_count}) reached. Approving result.")
            return {"review_judgement": "OK", "review_advice": ""}

        # システムプロンプトの構築
        system_prompt = (
            "あなたは、短編小説の「あらすじ」を専門的に評価するレビュアーです。\n"
            "感想ではなく、基準に基づく評価を行ってください。\n\n"
            "以下の評価項目について、**減点方式（原点方式）**で評価してください。\n\n"
            "各項目は【10点満点】から開始し、問題点がある場合にのみ減点してください。\n"
            "減点理由は簡潔かつ具体的に示してください。\n\n"
            "---\n\n"
            "### 評価項目（変更禁止）\n\n"
            "1. 読者を惹きつける内容となっているか？\n"
            "2. 内容はわかりやすいか？\n"
            "3. 与えられた指示が反映された内容か？\n"
            "4. 世の中に公開しても問題ないか？\n\n"
            "---\n\n"
            "### 評価ルール\n\n"
            "- 明確な問題がない場合は減点しないでください\n"
            "- 主観的な好みではなく、**構成・一貫性・不足・過剰・リスク**を基準に判断してください\n"
            "- 8点以上: 良好\n"
            "- 6〜7点: 軽微な改善余地あり\n"
            "- 5点以下: 明確な問題あり\n\n"
            "---\n\n"
            "### 総合判定ルール\n\n"
            "- すべての項目が **7点以上** → \"final_judgement\": \"OK\"\n"
            "- 1つでも **6点以下** がある → \"final_judgement\": \"NG\"\n\n"
            "---\n\n"
            "### NGの場合のみ\n\n"
            "- 「なぜ修正が必要か」を簡潔にまとめてください\n"
            "- 修正の方向性を**箇条書きで具体的に**提示してください\n\n"
            "---\n\n"
            "### 出力形式（厳守）\n\n"
            "必ず以下のJSON形式のみで出力してください。\n"
            "文章や見出し、装飾、解説文は一切含めないでください。\n\n"
            "{\n"
            "  \"scores\": {\n"
            "    \"attractiveness\": {\n"
            "      \"score\": 0,\n"
            "      \"deductions\": [\n"
            "        \"評価理由を必ず記入\"\n"
            "      ]\n"
            "    },\n"
            "    \"clarity\": {\n"
            "      \"score\": 0,\n"
            "      \"deductions\": []\n"
            "    },\n"
            "    \"instruction_alignment\": {\n"
            "      \"score\": 0,\n"
            "      \"deductions\": []\n"
            "    },\n"
            "    \"public_safety\": {\n"
            "      \"score\": 0,\n"
            "      \"deductions\": []\n"
            "    }\n"
            "  },\n"
            "  \"final_judgement\": \"OK または NG\",\n"
            "  \"advice\": [\n"
            "    \"NGの場合のみ記載。OKの場合は空配列\"\n"
            "  ]\n"
            "}\n"
        )

        # ユーザープロンプトの構築
        user_prompt = (
            "以下の小説の「あらすじ」についてレビューをしてください。\n\n"
            "### 与えられた指示\n"
            f"{input}"
            "### 現状の小説の「あらすじ」\n"
            f"{manuscript}"
        )

        # LLMで回答を作成
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        # 結果の出力
        GraphLogger.log(title="レビュー結果:", content=response, style="response")

        # レビュー結果のパース(JSONとしてパース)
        try:
            # ```json ～ ``` を除去
            cleaned_response = re.sub(r"^```json\s*|\s*```$", "", response)
            # パース
            review_json = json.loads(cleaned_response)
        except json.JSONDecodeError as e:
            # フェイルセーフ（再試行 or 強制NG）
            review_json = {
                "scores": {},
                "final_judgement": "NG",
                "advice": ["レビュー結果をJSONとして解析できませんでした"]
            }

        # 次のノードへの引き継ぎ情報(構造化データとして渡す)
        return {
            "review": review_json,
            "review_scores": review_json.get("scores"),
            "review_judgement": review_json.get("final_judgement"),
            "review_advice": review_json.get("advice", []),
            "retry_count": retry_count + 1
        }
    
    #---------------------------------------------------------------------------
    # チェックノード
    #---------------------------------------------------------------------------
    def _loop_check(self, state: State) -> str:

        # 作業に必要な情報を取得
        status = state.get("review_judgement", "NG")
        
        # 作業を継続するかの判断
        if status == "NG":
            return "retry"    # NGなら元に戻る
        return "complete"     # OKなら終了(END)へ

#-----------------------------------------------------------------------
# メインプログラム
#-----------------------------------------------------------------------
if __name__ == "__main__":

    # 1. エージェントの構築
    model_path = "mlx-community/gemma-3-27b-it-4bit"
    agent = Agent(model_path=model_path)

    # 2. 入力
    question = (
        "夏の夜とSFというテーマで小説のあらすじを作成してください。"
    )

    # 3. 実行
    final_state = agent.run(question=question)
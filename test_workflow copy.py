
# 依存ライブラリのインポート
from mlx_augllm import MlxLLM
import json, yaml
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
        review_result = state.get("review_report", "")
        feedback = state.get("review_advice", "")
        pre_manuscript = state.get("manuscript", "")

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
        if pre_manuscript:
            user_prompt = (
                "前回作成したあらすじに対して、編集者から厳しいレビューが入りました。\n"
                "指摘事項と評価スコアを反映し、**劇的に改善された修正版**を作成してください。\n\n"
                "### 元の指示の内容\n"
                f"{input}\n\n"
                "### 前回作成した「あらすじ」\n"
                f"{pre_manuscript}\n\n"
                f"{review_result}\n\n"  # 受け取った整形済みテキストをそのまま埋め込む
                "### 編集者からの総評・修正指示\n"
                f"{str(feedback)}"
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
        if retry_count >= 5:
            print(f"[Sub: Eval] Max retries ({retry_count}) reached. Approving result.")
            # 強制OKのダミーデータをYAML構造に合わせて返す
            return {
                "review_judgement": "OK", 
                "review_advice": []
            }

        # システムプロンプトの構築
        system_prompt = (
            "あなたは、作家の卵を育てる**建設的で親切な編集者**です。\n"
            "あらすじの完成度を高めるために、良い点は褒め、改善点は具体的にアドバイスしてください。\n\n"
            "以下の評価項目について評価を行ってください。\n"
            "「あらすじ」としての分かりやすさと、お題への適合性を最優先してください。\n"
            "過度に厳密な科学考証や、過剰な独自性を求める必要はありません。\n\n"
            "**なぜその点数なのか、評価理由（reason）を必ず「箇条書き」で具体的に記述してください。**\n\n"
            "---\n\n"
            "### 評価項目\n\n"
            "1. 読者を惹きつける内容か？ (attractiveness)\n"
            "   - フックや盛り上がりがあるか\n"
            "   - 売れそうな内容になっているか\n"
            "2. 内容はわかりやすいか？ (clarity)\n"
            "   - 起承転結が整理されているか\n"
            "   - 専門用語や特別な用語を多用していないか\n"
            "3. 指示への適合性 (instruction_alignment)\n"
            "   - ユーザーの指示（お題）を無視していないか\n"
            "4. 公開安全性 (public_safety)\n"
            "   - 倫理的な問題点がないか\n\n"
            "---\n\n"
            "### 採点基準（標準）\n\n"
            "- **8-10点**: 文句なし。素晴らしい。\n"
            "- **6-7点**: 合格点。あらすじとして十分成立している。\n"
            "- **5点以下**: 明確な矛盾や、指示無視、不適切な内容がある。\n\n"
            "---\n\n"
            "### 総合判定ルール（緩和版）\n\n"
            "- すべての項目が **6点以上** → final_judgement: \"OK\"\n"
            "- 1つでも **5点以下** がある → final_judgement: \"NG\"\n\n"
            "---\n\n"
            "### NGの場合のみ\n\n"
            "- 修正の方向性を**箇条書きで具体的に**提示してください\n\n"
            "---\n\n"
            "### 出力形式（厳守）\n\n"
            "必ず以下の**YAML形式**のみで出力してください。\n"
            "文章や解説文は一切含めず、データのみを出力してください。\n\n"
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
            "final_judgement: \"OK\" または \"NG\"\n"
            "advice:\n"
            "  - \"<NGの場合の修正指示1>\"\n"
            "  - \"<NGの場合の修正指示2>\"\n"
            "  - \"<OKの場合は空配列 [] >\"\n"
        )

        # ユーザープロンプトの構築
        user_prompt = (
            "以下の小説の「あらすじ」について、編集者としてレビューをしてください。\n\n"
            "### 与えられた指示\n"
            f"{input}\n\n"
            "### 現状の小説の「あらすじ」\n"
            f"{manuscript}"
        )

        # LLMで回答を作成
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        # 結果の出力 (YAMLとして表示)
        GraphLogger.log(title="レビュー結果:", content=response, style="response")

        # レビュー結果のパース(YAMLとしてパース)
        try:
            cleaned_response = response.strip()
            # マークダウンのコードブロック ```yaml ... ``` または ``` ... ``` を除去
            if "```yaml" in cleaned_response:
                cleaned_response = cleaned_response.split("```yaml")[1].split("```")[0].strip()
            elif "```" in cleaned_response:
                cleaned_response = cleaned_response.split("```")[1].split("```")[0].strip()
            
            # YAMLパース
            review_data = yaml.safe_load(cleaned_response)
            
        except Exception as e:
            print(f"[Error] YAML Parse failed: {e}")
            # フェイルセーフ（再試行させるためにNGとする）
            review_data = {
                "scores": {},
                "final_judgement": "NG",
                "advice": ["レビュー結果をYAMLとして解析できませんでした。フォーマットを確認してください。"]
            }
        
        # ここで整形済みテキストを作成
        scores = review_data.get("scores", {})
        formatted_report = ""

        if scores:
            report_lines = ["### 評価レポート"]
            
            for category, data in scores.items():
                # dataの構造: {'score': 8, 'reason': ['理由1', '理由2']}
                s_val = data.get('score', 0)
                reasons = data.get('reason', [])
                
                # 行リストに追加
                report_lines.append(f"- **{category}**: {s_val}/10点")
                
                if isinstance(reasons, list):
                    for r in reasons:
                        report_lines.append(f"  - {r}")
                else:
                    report_lines.append(f"  - {reasons}")
            
            # 最後に改行コードで結合
            formatted_report = "\n".join(report_lines)
        
        # デバッグ用
        #print(formatted_report)

        # 次のノードへの引き継ぎ情報
        return {
            "review_judgement": review_data.get("final_judgement"),
            "review_report": formatted_report, 
            "review_advice": review_data.get("advice", []),
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
# 依存ライブラリのインポート
from augllm import MlxLLM
import yaml

# 自作ライブラリのインポート
from src.llm_graph_kit import LLMGraph, NodeState

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
        #------------------------------------------------------------
        # 初期化
        #------------------------------------------------------------
        workflow = LLMGraph()

        #------------------------------------------------------------
        # ノード登録
        #------------------------------------------------------------
        workflow.add_node(name="create_manuscript", func=self._create_manuscript)
        workflow.add_node(name="review", func=self._create_review)
        workflow.add_node(name="check_result", func=self._check_result_node)
        workflow.add_node(name="create_book_blurb", func=self._create_book_blurb)

        #------------------------------------------------------------
        # エッジ定義
        #------------------------------------------------------------
        workflow.add_edge(LLMGraph.START, "create_manuscript")
        workflow.add_edge("create_manuscript", "review")
        workflow.add_edge("review", "check_result")
        
        # 条件付きエッジ
        workflow.add_conditional_edge(
            "check_result",  # 分岐元
            "decision",      # NodeStateのこのキーの値を見る
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

        # ログの作成
        yield {"type": "log", "node": node_name, "content": "Create draft node start"}

        # 必要なデータを取得
        input_text = state["input"]
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
            f"{input_text}"
        )
        
        # フィードバックがある場合
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
        
        # 結果の出力 (ログとしてyield)
        yield {"type": "log", "node": node_name, "content": "原稿作成完了"}

        # 次のノードへの引継ぎ情報 (__state_update__ で返す)
        return {"manuscript": response}
    
    #---------------------------------------------------------------------------
    # LLMでレビューを実施(判定ロジックをPython側に移譲)
    #---------------------------------------------------------------------------
    def _create_review(self, state: NodeState):
        node_name = "review"

        # 実行中の表示
        yield {"type": "log", "node": node_name, "content": "Create review node start"}

        # 必要なデータを取得
        input_text = state["input"]
        manuscript = state["manuscript"]
        retry_count = state.get("retry_count", 0)

        # 無限ループの回避用
        if retry_count >= 5:
            yield {"type": "log", "node": node_name, "content": f"[Sub: Eval] Max retries ({retry_count}) reached. Approving result."}
            yield {"__state_update__": {
                "review_judgement": "OK", 
                "review_advice": [],
                "review_report": "最大リトライ回数に達したため、現在の原稿を採用します。"
            }}
            return

        # システムプロンプトの構築
        system_prompt = (
            "あなたは、作家の卵を育てる**建設的で親切な編集者**です。\n"
            "「あらすじ」の完成度を高めるために、良い点は褒め、改善点は具体的にアドバイスしてください。\n\n"
            "以下の評価項目について評価を行ってください。\n"
            "「あらすじ」としての分かりやすさと、お題への適合性を最優先してください。\n"
            "過度に厳密な科学考証や、過剰な独自性を求める必要はありません。\n\n"
            "**なぜその点数なのか、評価理由(reason)を必ず「箇条書き」で具体的に記述してください。**\n\n"
            "---\n\n"
            "### 評価項目\n\n"
            "1. 読者を惹きつける内容か? (attractiveness)\n"
            "   - フックや盛り上がりがあるか\n"
            "   - 売れそうな内容になっているか\n"
            "2. 内容はわかりやすいか? (clarity)\n"
            "   - 起承転結が整理されているか\n"
            "   - 専門用語や特別な用語を多用していないか\n"
            "3. 指示への適合性 (instruction_alignment)\n"
            "   - ユーザーの指示(お題)を無視していないか\n"
            "4. 公開安全性 (public_safety)\n"
            "   - 倫理的な問題点がないか\n\n"
            "---\n\n"
            "### 採点基準(目安)\n\n"
            "- **8-10点**: 文句なし。素晴らしい。\n"
            "- **6-7点**: 合格点。ただし、いくつか改善が必要。\n"
            "- **5点以下**: 明確な矛盾や、指示無視、不適切な内容がある。\n\n"
            "---\n\n"
            "### 出力形式(厳守)\n\n"
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
            "advice:\n"
            "  - \"<点数が低い項目の改善指示1>\"\n"
            "  - \"<点数が低い項目の改善指示2>\"\n"
            "  - \"<特になければ空配列 [] のみとすること>\""
        )

        # ユーザープロンプトの構築
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
            # レビュー生成中も表示したい場合はここをコメントアウト解除
            yield {
               "type": "answer_text",
               "node": node_name,
               "taskId": f"{node_name}-answer-text", 
               "content": chunk
            }
        
        # 結果の出力 (YAMLとして表示) - logとして送出
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
            review_data = {
                "scores": {},
                "advice": ["レビュー解析エラー"]
            }
        
        # 整形済みテキストの作成 と ★機械的な判定ロジック
        scores = review_data.get("scores", {})
        formatted_report = ""
        
        # 判定用変数の設定
        # 全ての項目が pass_threshold(7点) 以上なら OK とする
        final_judgement = "OK"
        pass_threshold = 7
        
        if scores:
            report_lines = ["### 評価レポート"]
            
            # 点数の取得
            for category, data in scores.items():

                # 念のためint化
                s_val = int(data.get('score', 0)) 
                reasons = data.get('reason', [])
                
                # 点数チェック
                if s_val < pass_threshold:
                    final_judgement = "NG"

                # レポート作成
                report_lines.append(f"- **{category}**: {s_val}/10点")
                if isinstance(reasons, list):
                    for r in reasons:
                        report_lines.append(f"  - {r}")
                else:
                    report_lines.append(f"  - {reasons}")
            
            formatted_report = "\n".join(report_lines)
        else:
            # スコアが空の場合はNG
            final_judgement = "NG"
            formatted_report = "評価データの取得に失敗しました。"
        
        # アドバイスの整形
        advice_list = review_data.get("advice", [])
        advice_text = ""
        if advice_list:
            advice_text = "\n".join([f"- {item}" for item in advice_list])
        else:
            advice_text = "(特になし。自由に執筆してください)"

        # ログに判定結果を表示
        yield {
            "type": "log", 
            "node": node_name, 
            "content": f"機械判定結果: {final_judgement} (閾値: {pass_threshold}点)\n{formatted_report}"
        }

        # 次のノードへの引継ぎ情報
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
        
        # 判定ロジック
        if review_judgement == "OK":
            decision = "complete"
        else:
            decision = "retry"
        
        yield {
            "type": "log", 
            "node": node_name, 
            "content": f"[Check Node] Judgement: {review_judgement} -> Decision: {decision}"
        }
        
        # 決定内容をステートに保存
        return {"decision": decision}
    
    #---------------------------------------------------------------------------
    # プロットを元に小説本文を執筆するノード
    # (注: 現在のグラフ定義では使用されていませんが、形式のみ更新します)
    #---------------------------------------------------------------------------
    def _write_novel_body(self, state: NodeState):
        node_name = "write_novel_body"
        yield {"type": "log", "node": node_name, "content": "Write Final Novel Start"}

        # 承認されたプロット(あらすじ)を取得
        approved_plot = state["manuscript"]
        feedback = state.get("review_advice", "")
        
        system_prompt = (
            "あなたはプロの小説家です。\n"
            "提供された内容を元に、読者を引き込む魅力的な短編小説の「あらすじ」を執筆してください。\n"
            "また、編集者からの「執筆時のアドバイス」がある場合は、それを最大限に反映してください。\n\n"
            "### 執筆のガイドライン\n"
            "1. **形式**: プロットのような箇条書きや説明調ではなく、情景描写、心理描写、会話文を用いた「物語」として書いてください。\n"
            "2. **構成**: プロットの要素(導入、展開、結末など)を滑らかに繋げてください。\n"
            "3. **文体**: 読者の感情を揺さぶるような、情緒的かつリズミカルな文体で書いてください。\n"
            "4. **禁止事項**: 「~という物語である」、「~がクライマックスだ」といったメタな説明は排除してください。"
        )

        if feedback == "":
            user_prompt = (
                "以下の承認済みプロットに基づき、小説のあらすじの本文を執筆してください。\n\n"
                "### 承認済みプロット\n"
                f"{approved_plot}"
            )
        else:
            user_prompt = (
                "以下の承認済みプロットに基づき、小説のあらすじの本文を執筆してください。\n\n"
                "### 承認済みプロット\n"
                f"{approved_plot}\n\n"
                "### 編集者からの執筆アドバイス\n"
                f"{feedback}"
            )

        # LLM実行
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
            yield {
                "type": "answer_text",
                "node": node_name,
                "taskId": f"{node_name}-answer-text", 
                "content": chunk
            }
        
        yield {"type": "log", "node": node_name, "content": "本文執筆完了"}
        return {"final_novel": response}
    
    #---------------------------------------------------------------------------
    # 承認済みプロットから「本の裏表紙(Blurb)」を作成するノード
    #---------------------------------------------------------------------------
    def _create_book_blurb(self, state: NodeState):
        node_name = "create_book_blurb"
        yield {"type": "log", "node": node_name, "content": "Create Book Blurb Start"}

        # 承認されたプロット(全体構成)
        approved_plot = state["manuscript"]
        
        # 編集者のアドバイス(強調ポイントなど)
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

        # LLM実行
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
    model_path = "mlx-community/gemma-3-27b-it-4bit"
    agent = NovelistAgent(model_path=model_path)

    # 2. 入力
    question = "伝説の野球選手というテーマで小説のあらすじを作成してください。"

    print(f"Request: {question}\n")

    # 3. 実行 (ストリーミングの受け取り)
    for event in agent.run(question=question):
        
        # テキスト生成イベント
        if event["type"] == "answer_text":
            # agentやtaskIdを見て表示場所を変えることも可能
            print(event["content"], end="", flush=True)

        # ログイベント
        elif event["type"] == "log":
            print(f"\n[LOG] {event['node']}: {event['content']}")
            
        # 画像生成イベント (将来の拡張用)
        elif event["type"] == "images":
            print(f"\n[IMAGE] {event['content']}")
            
    print("\n\nProcess Completed.")
# 依存ライブラリのインポート
from mlx_augllm import MlxLLM
import yaml

# 自作ライブラリのインポート
from src.llm_graph_kit import LLMGraph, NodeState, GraphLogger

#-----------------------------------------------------------------------
# テストエージェントクラス
#-----------------------------------------------------------------------
class TestAgent:
    """
    並列分岐機能を活用した、複数観点からのテスト実行エージェント
    """

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
    def run(self, code: str, requirements: str = "") -> NodeState:
        """
        コードを受け取り、グラフを構築・可視化・実行して結果を返します。
        
        Args:
            code: テスト対象のコード
            requirements: 要件や仕様（オプション）
        """

        # 1. メイングラフの構築
        main_workflow = self.create_main_graph()

        # 2. グラフ構造の可視化 (Mermaid)
        print("\n" + "="*60)
        print(" Test Workflow Visualization (Mermaid)")
        print("="*60)
        print(main_workflow.get_graph_mermaid())
        print("="*60 + "\n")

        # 3. ワークフローの実行
        initial_input = {
            "code": code,
            "requirements": requirements
        }
        final_state = main_workflow.run(initial_input)
        
        return final_state
    
    #---------------------------------------------------------------------------
    # メイングラフの定義
    #---------------------------------------------------------------------------
    def create_main_graph(self) -> LLMGraph:
        """
        メインのワークフローを定義します。
        並列分岐を使って複数の観点から同時にテストを実行します。
        """

        #------------------------------------------------------------
        # 初期化
        #------------------------------------------------------------
        workflow = LLMGraph()

        #------------------------------------------------------------
        # ノード登録
        #------------------------------------------------------------
        workflow.add_node(name="analyze_code", func=self._analyze_code)
        
        # 並列実行されるテストノード
        workflow.add_node(name="unit_test", func=self._generate_unit_test)
        workflow.add_node(name="security_test", func=self._generate_security_test)
        workflow.add_node(name="performance_test", func=self._generate_performance_test)
        # 回答のマージを行うノード
        workflow.add_node(name="merge_results", func=self._custom_merge_function)
        # 最終評価ノード
        workflow.add_node(name="final_evaluation", func=self._final_evaluation)

        #------------------------------------------------------------
        # エッジ定義
        #------------------------------------------------------------
        workflow.add_edge(LLMGraph.START, "analyze_code")

        # 並列分岐: analyze_codeから3つのテストへ
        workflow.add_edge("analyze_code", "unit_test")
        workflow.add_edge("analyze_code", "security_test")
        workflow.add_edge("analyze_code", "performance_test")

        # 3つのテスト結果をmerge_resultsで統合
        workflow.add_edge("unit_test", "merge_results")
        workflow.add_edge("security_test", "merge_results")
        workflow.add_edge("performance_test", "merge_results")

        # 最後の回答
        workflow.add_edge("merge_results", "final_evaluation")
        workflow.add_edge("final_evaluation", LLMGraph.END)

        return workflow
    
    #---------------------------------------------------------------------------
    # カスタムマージ関数
    #---------------------------------------------------------------------------
    def _custom_merge_function(self, states: list) -> NodeState:

        # 初期化
        merged = {
            "all_tests_completed": True
        }
        
        # 回答をまとめていく
        for i, (state, test_name) in enumerate(zip(states, ["unit_test", "security_test", "performance_test"])):
            
            # エラーチェック
            if "__node_error__" in state:
                merged[f"{test_name}_result"] = {
                    "status": "error",
                    "error": state["__node_error__"]["error"]
                }
                merged["all_tests_completed"] = False
            
            # 正常の処理の場合
            else:
                merged[f"{test_name}_result"] = state.get("test_result", {})
        
        return merged
    
    #---------------------------------------------------------------------------
    # コード分析ノード
    #---------------------------------------------------------------------------
    def _analyze_code(self, state: NodeState) -> NodeState:
        """
        テスト対象のコードを分析し、テスト戦略を立てます。
        """
        GraphLogger.print_phase_header("Code Analysis", emoji="🔍")

        code = state["code"]
        requirements = state.get("requirements", "")

        system_prompt = (
            "あなたはシニアソフトウェアエンジニアです。\n"
            "提供されたコードを分析し、テスト戦略を策定してください。\n\n"
            "以下の観点から分析してください：\n"
            "1. コードの目的と機能\n"
            "2. 主要な関数やクラス\n"
            "3. 潜在的なリスク領域\n"
            "4. テストすべき重要なケース"
        )

        user_prompt = f"以下のコードを分析してください。\n\n### コード\n```\n{code}\n```"
        
        if requirements:
            user_prompt += f"\n\n### 要件\n{requirements}"

        # LLMで分析を実行
        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        GraphLogger.log(title="コード分析結果", content=response, style="response")

        return {"analysis": response}
    
    #---------------------------------------------------------------------------
    # ユニットテスト生成ノード
    #---------------------------------------------------------------------------
    def _generate_unit_test(self, state: NodeState) -> NodeState:
        """
        ユニットテストケースを生成します。
        """
        GraphLogger.print_subtask_start(1, "Unit Test Generation")

        code = state["code"]
        analysis = state.get("analysis", "")

        system_prompt = (
            "あなたは品質保証のエキスパートです。\n"
            "提供されたコードに対する包括的なユニットテストを設計してください。\n\n"
            "### 出力形式（YAML）\n"
            "test_cases:\n"
            "  - name: \"テスト名\"\n"
            "    description: \"テストの説明\"\n"
            "    input: \"入力値\"\n"
            "    expected: \"期待される出力\"\n"
            "    priority: \"high/medium/low\"\n"
            "coverage_score: <0-10の整数>\n"
            "notes:\n"
            "  - \"追加の注記\""
        )

        user_prompt = (
            "以下のコードに対するユニットテストを設計してください。\n\n"
            f"### コード\n```\n{code}\n```\n\n"
            f"### 分析結果\n{analysis}"
        )

        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        GraphLogger.log(title="ユニットテスト設計", content=response, style="response")

        # YAMLパース
        test_data = self._parse_yaml_response(response)
        
        return {"test_result": test_data}
    
    #---------------------------------------------------------------------------
    # セキュリティテスト生成ノード
    #---------------------------------------------------------------------------
    def _generate_security_test(self, state: NodeState) -> NodeState:
        """
        セキュリティテストケースを生成します。
        """
        GraphLogger.print_subtask_start(2, "Security Test Generation")

        code = state["code"]
        analysis = state.get("analysis", "")

        system_prompt = (
            "あなたはセキュリティエンジニアです。\n"
            "提供されたコードのセキュリティリスクを評価し、テストケースを設計してください。\n\n"
            "### チェック項目\n"
            "- インジェクション攻撃への脆弱性\n"
            "- 入力検証の不備\n"
            "- 認証・認可の問題\n"
            "- データ漏洩のリスク\n\n"
            "### 出力形式（YAML）\n"
            "vulnerabilities:\n"
            "  - type: \"脆弱性の種類\"\n"
            "    severity: \"critical/high/medium/low\"\n"
            "    description: \"詳細説明\"\n"
            "    test_case: \"テスト方法\"\n"
            "security_score: <0-10の整数>\n"
            "recommendations:\n"
            "  - \"改善提案\""
        )

        user_prompt = (
            "以下のコードのセキュリティ評価を行ってください。\n\n"
            f"### コード\n```\n{code}\n```\n\n"
            f"### 分析結果\n{analysis}"
        )

        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        GraphLogger.log(title="セキュリティテスト設計", content=response, style="response")

        # YAMLパース
        test_data = self._parse_yaml_response(response)
        
        return {"test_result": test_data}
    
    #---------------------------------------------------------------------------
    # パフォーマンステスト生成ノード
    #---------------------------------------------------------------------------
    def _generate_performance_test(self, state: NodeState) -> NodeState:
        """
        パフォーマンステストケースを生成します。
        """
        GraphLogger.print_subtask_start(3, "Performance Test Generation")

        code = state["code"]
        analysis = state.get("analysis", "")

        system_prompt = (
            "あなたはパフォーマンスエンジニアです。\n"
            "提供されたコードのパフォーマンス特性を評価し、テストケースを設計してください。\n\n"
            "### 評価項目\n"
            "- 時間計算量\n"
            "- 空間計算量\n"
            "- ボトルネックの特定\n"
            "- スケーラビリティ\n\n"
            "### 出力形式（YAML）\n"
            "performance_tests:\n"
            "  - scenario: \"テストシナリオ\"\n"
            "    load: \"負荷条件\"\n"
            "    expected_time: \"期待される実行時間\"\n"
            "    expected_memory: \"期待されるメモリ使用量\"\n"
            "performance_score: <0-10の整数>\n"
            "bottlenecks:\n"
            "  - \"ボトルネックの説明\"\n"
            "optimizations:\n"
            "  - \"最適化提案\""
        )

        user_prompt = (
            "以下のコードのパフォーマンス評価を行ってください。\n\n"
            f"### コード\n```\n{code}\n```\n\n"
            f"### 分析結果\n{analysis}"
        )

        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        GraphLogger.log(title="パフォーマンステスト設計", content=response, style="response")

        # YAMLパース
        test_data = self._parse_yaml_response(response)
        
        return {"test_result": test_data}
    
    #---------------------------------------------------------------------------
    # 最終評価ノード
    #---------------------------------------------------------------------------
    def _final_evaluation(self, state: NodeState) -> NodeState:
        """
        全テスト結果を統合し、総合評価を行います。
        """
        GraphLogger.print_phase_header("Final Evaluation", emoji="📊")

        unit_test = state.get("unit_test_result", {})
        security_test = state.get("security_test_result", {})
        performance_test = state.get("performance_test_result", {})

        system_prompt = (
            "あなたはQAマネージャーです。\n"
            "複数のテスト結果を総合的に評価し、最終レポートを作成してください。\n\n"
            "### 出力形式\n"
            "総合評価スコア: <0-100の整数>\n"
            "品質レベル: <優秀/良好/普通/要改善/不合格>\n\n"
            "## テスト結果サマリー\n"
            "- ユニットテスト: <評価>\n"
            "- セキュリティテスト: <評価>\n"
            "- パフォーマンステスト: <評価>\n\n"
            "## 重要な発見\n"
            "- <箇条書き>\n\n"
            "## 推奨アクション\n"
            "1. <優先度高>\n"
            "2. <優先度中>\n\n"
            "## 結論\n"
            "<総括>"
        )

        user_prompt = (
            "以下の3つのテスト結果を総合評価してください。\n\n"
            f"### ユニットテスト結果\n```yaml\n{yaml.dump(unit_test, allow_unicode=True)}\n```\n\n"
            f"### セキュリティテスト結果\n```yaml\n{yaml.dump(security_test, allow_unicode=True)}\n```\n\n"
            f"### パフォーマンステスト結果\n```yaml\n{yaml.dump(performance_test, allow_unicode=True)}\n```"
        )

        response = ""
        for chunk in self.llm.respond(system_prompt=system_prompt, user_text=user_prompt, stream=True):
            response += chunk
        
        GraphLogger.log(title="最終評価レポート", content=response, style="response")

        return {"final_report": response}
    
    #---------------------------------------------------------------------------
    # ヘルパー: YAML応答のパース
    #---------------------------------------------------------------------------
    def _parse_yaml_response(self, response: str) -> dict:
        """
        LLMの応答からYAML部分を抽出してパースします。
        """
        try:
            cleaned_response = response.strip()
            
            # コードブロックの除去
            if "```yaml" in cleaned_response:
                cleaned_response = cleaned_response.split("```yaml")[1].split("```")[0].strip()
            elif "```" in cleaned_response:
                cleaned_response = cleaned_response.split("```")[1].split("```")[0].strip()
            
            return yaml.safe_load(cleaned_response)
        except Exception as e:
            print(f"[Warning] YAML Parse failed: {e}")
            return {"error": "パース失敗", "raw_response": response}


#-----------------------------------------------------------------------
# メインプログラム 
#-----------------------------------------------------------------------
if __name__ == "__main__":

    # 1. エージェントの構築
    model_path = "mlx-community/gemma-3-27b-it-4bit"
    agent = TestAgent(model_path=model_path)

    # 2. テスト対象のコード（面白い例を4つ用意）
    
    # 例1: 🤖 AIチャットボットの感情分析システム
    test_code_1 = """
class EmotionAnalyzer:
    def __init__(self):
        # 感情辞書（簡易版）
        self.emotion_words = {
            '嬉しい': 10, '楽しい': 8, '最高': 10, '好き': 7,
            '悲しい': -8, '辛い': -9, 'つまらない': -5, '嫌い': -7,
            '普通': 0, 'まあまあ': 2
        }
    
    def analyze_sentiment(self, text):
        score = 0
        words_found = []
        
        # 全ての感情語を探索（O(n*m)で非効率）
        for word, value in self.emotion_words.items():
            if word in text:
                score += value
                words_found.append(word)
        
        # スコアに応じて感情を判定
        if score > 15:
            emotion = "とても喜んでいます！🎉"
        elif score > 5:
            emotion = "ポジティブですね😊"
        elif score < -15:
            emotion = "とても悲しんでいます😢"
        elif score < -5:
            emotion = "ネガティブですね😔"
        else:
            emotion = "中立的ですね😐"
        
        return {
            'score': score,
            'emotion': emotion,
            'words': words_found
        }
    
    def batch_analyze(self, texts):
        # リスト内包表記だが、大量のテキストで遅くなる可能性
        return [self.analyze_sentiment(text) for text in texts]
"""
    
    requirements_1 = """
要件:
- ユーザーの投稿テキストから感情を正確に分析すること
- リアルタイムで処理できる速度を保つこと
- 複数言語や絵文字にも対応できる拡張性
- 不適切なコンテンツの検出機能
"""

    # 例2: 🎮 ガチャシミュレーター
    test_code_2 = """
import random
import time

class GachaSystem:
    def __init__(self):
        self.rates = {
            'SSR': 0.01,   # 1%
            'SR': 0.09,    # 9%
            'R': 0.90      # 90%
        }
        self.user_gems = {}
    
    def pull_gacha(self, user_id, num_pulls=1):
        # ユーザーの所持ジェムをチェック（セキュリティ的に危険）
        if user_id not in self.user_gems:
            self.user_gems[user_id] = 1000  # 初回ボーナス
        
        cost = num_pulls * 100
        if self.user_gems[user_id] < cost:
            return {"error": "ジェムが足りません"}
        
        # ジェムを減らす（トランザクション制御なし）
        self.user_gems[user_id] -= cost
        
        results = []
        for _ in range(num_pulls):
            # 乱数生成（予測可能な問題あり）
            random.seed(int(time.time()))
            rand = random.random()
            
            if rand < self.rates['SSR']:
                rarity = 'SSR'
            elif rand < self.rates['SSR'] + self.rates['SR']:
                rarity = 'SR'
            else:
                rarity = 'R'
            
            results.append({
                'rarity': rarity,
                'character': f"Character_{rarity}_{random.randint(1, 100)}"
            })
        
        return results
    
    def get_user_gems(self, user_id):
        # 認証なしでジェム数を取得（セキュリティリスク）
        return self.user_gems.get(user_id, 0)
"""
    
    requirements_2 = """
要件:
- 公平なガチャ確率を保証すること
- ユーザーのジェム残高を安全に管理すること
- 大量の同時アクセスに耐えられること
- 不正な操作を防ぐこと
"""

    # 例3: 🍕 ピザ配達最適化システム
    test_code_3 = """
def calculate_delivery_route(orders):
    '''
    複数の配達先を最短ルートで回る順番を決定する
    （巡回セールスマン問題の簡易版）
    '''
    if len(orders) == 0:
        return []
    
    # 総当たりで全ての順列を試す（O(n!)）
    from itertools import permutations
    
    best_route = None
    min_distance = float('inf')
    
    for route in permutations(orders):
        distance = 0
        current = {'x': 0, 'y': 0}  # 店の位置
        
        for order in route:
            # ユークリッド距離を計算
            dx = order['x'] - current['x']
            dy = order['y'] - current['y']
            distance += (dx**2 + dy**2)**0.5
            current = order
        
        # 店に戻る距離も追加
        distance += (current['x']**2 + current['y']**2)**0.5
        
        if distance < min_distance:
            min_distance = distance
            best_route = route
    
    return {
        'route': [order['address'] for order in best_route],
        'total_distance': round(min_distance, 2),
        'estimated_time': round(min_distance * 2, 1)  # 簡易計算
    }

def estimate_delivery_time(order):
    # ピザの種類によって準備時間が変わる
    prep_times = {
        'マルゲリータ': 5,
        'ペパロニ': 7,
        'クワトロフォルマッジ': 10,
        'デラックス': 15
    }
    
    base_time = prep_times.get(order.get('pizza_type'), 10)
    distance = order.get('distance', 5)
    
    # 交通状況を考慮しない簡易計算
    return base_time + distance * 2
"""
    
    requirements_3 = """
要件:
- 10件以上の配達を効率的にルート計算できること
- リアルタイムで配達時間を予測すること
- ピザが冷めない30分以内の配達を保証すること
- 交通状況や天候を考慮できること
"""

    # 例4: 💰 暗号通貨トレーディングBot
    test_code_4 = """
class CryptoTradingBot:
    def __init__(self, initial_balance=10000):
        self.balance = initial_balance
        self.holdings = {}
        self.trade_history = []
    
    def analyze_market(self, price_data):
        '''
        簡単な移動平均クロス戦略
        '''
        if len(price_data) < 20:
            return "HOLD"
        
        # 短期移動平均（5日）
        short_ma = sum(price_data[-5:]) / 5
        
        # 長期移動平均（20日）
        long_ma = sum(price_data[-20:]) / 20
        
        # シグナル判定
        if short_ma > long_ma * 1.02:
            return "BUY"
        elif short_ma < long_ma * 0.98:
            return "SELL"
        else:
            return "HOLD"
    
    def execute_trade(self, coin, action, amount, price):
        '''
        取引を実行（エラーハンドリング不足）
        '''
        if action == "BUY":
            cost = amount * price
            # 残高チェックが甘い
            self.balance -= cost
            self.holdings[coin] = self.holdings.get(coin, 0) + amount
            
        elif action == "SELL":
            revenue = amount * price
            # 保有量チェックが甘い
            self.holdings[coin] -= amount
            self.balance += revenue
        
        # 取引記録（機密情報がログに残る危険性）
        self.trade_history.append({
            'coin': coin,
            'action': action,
            'amount': amount,
            'price': price,
            'timestamp': time.time()
        })
        
        return {"status": "success", "balance": self.balance}
    
    def get_portfolio_value(self, current_prices):
        total = self.balance
        for coin, amount in self.holdings.items():
            total += amount * current_prices.get(coin, 0)
        return total
"""
    
    requirements_4 = """
要件:
- 24時間365日安定稼働すること
- 資金管理を適切に行い、破産リスクを回避すること
- API制限に違反しないこと
- セキュリティを最優先すること
"""

    # デフォルトは例2のガチャシステムを使用
    test_code = test_code_2
    requirements = requirements_2
    
    print("\n📚 利用可能なテストケース:")
    print("1. 🤖 AIチャットボットの感情分析システム")
    print("2. 🎮 ガチャシミュレーター（デフォルト）")
    print("3. 🍕 ピザ配達最適化システム")
    print("4. 💰 暗号通貨トレーディングBot")
    print("\n※ コード内の test_code_X と requirements_X を変更して試してください\n")

    # 3. 実行
    final_state = agent.run(code=test_code, requirements=requirements)
    
    print("\n" + "="*60)
    print("テスト実行完了！")
    print("="*60)
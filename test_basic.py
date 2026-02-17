# 最新のライブラリ名に合わせてインポート
from src.llm_graph_kit import LLMGraph

# ============================================================
# 使用例
# ============================================================
if __name__ == "__main__":
    
    print("="*60)
    print("LangGraphスタイルの並列実行デモ (Generator Version)")
    print("="*60 + "\n")
    
    # グラフの作成
    workflow = LLMGraph()
    
    # ------------------------------------------------------------
    # ノード定義
    # 最新仕様: 
    # 1. ログやストリーミングは `yield` する
    # 2. ステート更新は `return` する
    # ------------------------------------------------------------
    
    def analyze(state):
        # ログをイベントとして送出
        yield {"type": "log", "node": "analyze", "content": f"📊 Analyzing: {state.get('input')}"}
        # 次のステップへのデータ更新
        return {"analysis": "Code analysis complete"}
    
    def unit_test(state):
        yield {"type": "log", "node": "unit_test", "content": "🧪 Running unit tests..."}
        return {"unit_result": "All unit tests passed"}
    
    def security_test(state):
        yield {"type": "log", "node": "security_test", "content": "🔒 Running security tests..."}
        return {"security_result": "No vulnerabilities found"}
    
    def performance_test(state):
        yield {"type": "log", "node": "performance_test", "content": "⚡ Running performance tests..."}
        return {"performance_result": "Performance within acceptable range"}
    
    def merge_results(states):
        """
        マージ関数: List[NodeState] を受け取り、統合結果を返す
        （通常の関数として定義しても、ライブラリ側で正しく処理されます）
        """
        # yield を使わない場合、標準出力に出ても良いが、
        # 統一感を出すならここもジェネレータにするのがベター。
        # 今回はシンプルに標準関数のまま return する例とします。
        
        merged = {}
        for i, (state, test_name) in enumerate(zip(states, ["unit", "security", "performance"])):
            if "__node_error__" in state:
                merged[f"{test_name}_result"] = f"Error: {state['__node_error__']['error']}"
            else:
                merged[f"{test_name}_result"] = state.get(f"{test_name}_result", "No result")
        
        return merged
    
    def final_eval(state):
        yield {"type": "log", "node": "final_eval", "content": "📋 Creating final evaluation report..."}
        
        report = (
            "Final Report:\n"
            f"- Unit Tests: {state.get('unit_result', 'N/A')}\n"
            f"- Security Tests: {state.get('security_result', 'N/A')}\n"
            f"- Performance Tests: {state.get('performance_result', 'N/A')}\n"
        )
        
        # 最終結果を特定イベントとして送出（メインループで受け取るため）
        yield {"type": "final_result", "content": report.strip()}
        
        return {"final_report": report.strip()}
    
    # ===== LangGraphスタイルでグラフを構築 =====
    
    # 1. ノードを登録
    workflow.add_node("analyze", analyze)
    workflow.add_node("unit_test", unit_test)
    workflow.add_node("security_test", security_test)
    workflow.add_node("performance_test", performance_test)
    workflow.add_node("merge_results", merge_results)
    workflow.add_node("final_eval", final_eval)
    
    # 2. エッジを定義
    workflow.add_edge(LLMGraph.START, "analyze")
    
    # 並列分岐
    workflow.add_edge("analyze", "unit_test")
    workflow.add_edge("analyze", "security_test")
    workflow.add_edge("analyze", "performance_test")
    
    # マージ
    workflow.add_edge("unit_test", "merge_results")
    workflow.add_edge("security_test", "merge_results")
    workflow.add_edge("performance_test", "merge_results")
    
    # 最終評価
    workflow.add_edge("merge_results", "final_eval")
    workflow.add_edge("final_eval", LLMGraph.END)
    
    # 可視化
    print("\n📊 Workflow Visualization (Mermaid):")
    print("-" * 60)
    print(workflow.get_graph_mermaid())
    print("-" * 60 + "\n")
    
    # 実行
    print("🚀 Executing workflow...\n")
    
    final_result_text = "No report generated."
    
    # ジェネレータとして実行し、イベントを処理
    for event in workflow.run({"input": "sample_code.py"}):
        
        if event["type"] == "log":
            # ノードからのログを表示
            print(f"  [{event['node']}] {event['content']}")
            
        elif event["type"] == "error":
            print(f"\n⚠️  Error in {event['node']}: {event['content']}")
            
        elif event["type"] == "final_result":
            # 最終結果をキャプチャ
            final_result_text = event["content"]

    print("\n" + "="*60)
    print("✅ Workflow completed!")
    print("="*60)
    print("\n📄 Final Result:")
    print(final_result_text)
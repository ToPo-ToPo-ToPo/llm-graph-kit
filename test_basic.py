

# 自作ライブラリのインポート
from src.llm_graph_kit import LLMGraph

# ============================================================
# 使用例
# ============================================================
if __name__ == "__main__":
    
    print("="*60)
    print("LangGraphスタイルの並列実行デモ")
    print("="*60 + "\n")
    
    # グラフの作成
    workflow = LLMGraph()
    
    # ノード定義
    def analyze(state):
        print(f"📊 Analyzing: {state.get('input')}")
        return {"analysis": "Code analysis complete"}
    
    def unit_test(state):
        print("🧪 Running unit tests...")
        return {"unit_result": "All unit tests passed"}
    
    def security_test(state):
        print("🔒 Running security tests...")
        return {"security_result": "No vulnerabilities found"}
    
    def performance_test(state):
        print("⚡ Running performance tests...")
        return {"performance_result": "Performance within acceptable range"}
    
    def merge_results(states):
        """
        マージ関数: List[NodeState] を受け取り、統合結果を返す
        """
        print(f"🔗 Merging {len(states)} parallel results...")
        
        merged = {}
        for i, (state, test_name) in enumerate(zip(states, ["unit", "security", "performance"])):
            if "__node_error__" in state:
                merged[f"{test_name}_result"] = f"Error: {state['__node_error__']['error']}"
            else:
                # 各テストの結果を抽出
                merged[f"{test_name}_result"] = state.get(f"{test_name}_result", "No result")
        
        return merged
    
    def final_eval(state):
        print("📋 Creating final evaluation report...")
        report = (
            "Final Report:\n"
            f"- Unit Tests: {state.get('unit_result', 'N/A')}\n"
            f"- Security Tests: {state.get('security_result', 'N/A')}\n"
            f"- Performance Tests: {state.get('performance_result', 'N/A')}\n"
        )
        return {"final_report": report.strip()}
    
    # ===== LangGraphスタイルでグラフを構築 =====
    
    # 1. ノードを登録
    workflow.add_node("analyze", analyze)
    workflow.add_node("unit_test", unit_test)
    workflow.add_node("security_test", security_test)
    workflow.add_node("performance_test", performance_test)
    workflow.add_node("merge_results", merge_results)  # マージノードも普通のノード
    workflow.add_node("final_eval", final_eval)
    
    # 2. エッジを定義（シンプル！）
    workflow.add_edge(LLMGraph.START, "analyze")
    
    # 並列分岐: analyzeから3つのテストへ
    workflow.add_edge("analyze", "unit_test")
    workflow.add_edge("analyze", "security_test")
    workflow.add_edge("analyze", "performance_test")
    
    # 3つのテスト結果をmerge_resultsで統合
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
    result = workflow.run({"input": "sample_code.py"})
    
    print("\n" + "="*60)
    print("✅ Workflow completed!")
    print("="*60)
    print("\n📄 Final Result:")
    print(result.get("final_report", "No report"))
    
    if result.get("__errors__"):
        print("\n⚠️  Errors encountered:")
        for err in result["__errors__"]:
            print(f"  - {err['node']}: {err['error']}")
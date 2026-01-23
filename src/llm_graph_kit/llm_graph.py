from typing import Dict, Any, Callable, Union, Tuple, List
import copy

# ステートの型定義
NodeState = Dict[str, Any]

class LLMGraph:
    """
    ノードとエッジで構成されるステートマシンエンジン。
    Router関数だけでなく、ステートの値を直接参照するルーティングに対応。
    並列分岐・合流機能を追加しました。
    """
    # 定数定義
    START = "__START__"
    END = "__END__"

    def __init__(self):
        # ノード名 -> 関数
        self.nodes: Dict[str, Callable[[NodeState], NodeState]] = {}
        # ノード名 -> 次のノード名 または (条件, マッピング辞書) または List[str](並列分岐)
        self.edges: Dict[str, Union[str, Tuple[Union[Callable, str], Dict[str, str]], List[str]]] = {} 
        self.entry_point: str = ""
        self.subgraphs: Dict[str, 'LLMGraph'] = {}
        # 合流ノード情報: {合流ノード名: (分岐元ノード名, 結果統合関数)}
        self.merge_nodes: Dict[str, Tuple[str, Callable[[List[NodeState]], NodeState]]] = {}

    def add_node(self, name: str, func: Callable[[NodeState], NodeState]):
        """ノードを登録します"""
        self.nodes[name] = func
    
    def add_node_with_subgraph(self, name: str, func: Callable[[NodeState], NodeState], subgraph: 'LLMGraph'):
        """サブグラフ構造を持つノードを登録します"""
        self.nodes[name] = func
        self.subgraphs[name] = subgraph

    def add_edge(self, from_node: str, to_node: str):
        """固定ルートを定義します"""
        if from_node == self.START:
            self.entry_point = to_node
            return
        self.edges[from_node] = to_node

    def add_conditional_edge(
        self, 
        from_node: str, 
        condition: Union[Callable[[NodeState], str], str], 
        path_map: Dict[str, str]
    ):
        """
        条件分岐ルートを定義します。
        
        Args:
            from_node: 分岐元のノード名
            condition: 
                - 関数: NodeStateを受け取りシグナル(文字列)を返す
                - 文字列: シグナルが格納されているNodeStateのキー名
            path_map: { "シグナル": "行き先のノード名" } の辞書
        """
        self.edges[from_node] = (condition, path_map)

    def add_parallel_edges(
        self,
        from_node: str,
        to_nodes: List[str],
        merge_node: str,
        merge_func: Callable[[List[NodeState]], NodeState] = None
    ):
        """
        並列分岐を定義します。
        
        Args:
            from_node: 分岐元のノード名
            to_nodes: 並列実行するノード名のリスト
            merge_node: 結果を統合する合流ノード名
            merge_func: 複数の結果を統合する関数 (デフォルトは結果を配列に格納)
        """
        self.edges[from_node] = to_nodes
        
        # デフォルトのマージ関数: 各結果を配列に格納
        if merge_func is None:
            def default_merge(states: List[NodeState]) -> NodeState:
                return {"parallel_results": states}
            merge_func = default_merge
        
        self.merge_nodes[merge_node] = (from_node, merge_func)

    def add_subgraph(self, node_name: str, subgraph: 'LLMGraph'):
        """可視化用にサブグラフを登録します"""
        self.subgraphs[node_name] = subgraph


    def run(self, initial_state: NodeState) -> NodeState:
        """グラフを実行します"""
        if not self.entry_point:
            raise ValueError("Entry point not set. Use add_edge(Graph.START, 'node_name').")

        current_node_name = self.entry_point
        state = initial_state.copy()
        
        # エラー記録用
        state["__errors__"] = []

        while current_node_name != self.END:
            # 合流ノードかチェック
            if current_node_name in self.merge_nodes:
                branch_node, merge_func = self.merge_nodes[current_node_name]
                print(f"[Merge Node]: Combining results at '{current_node_name}'")
                
                # 並列実行された結果を統合
                parallel_states = state.get("__parallel_states__", [])
                merged_result = merge_func(parallel_states)
                state.update(merged_result)
                
                # 一時的な並列結果を削除
                if "__parallel_states__" in state:
                    del state["__parallel_states__"]
                
                # 合流ノード自体の実行
                if current_node_name in self.nodes:
                    node_func = self.nodes[current_node_name]
                    try:
                        new_data = node_func(state)
                        if new_data:
                            state.update(new_data)
                    except Exception as e:
                        error_info = {
                            "node": current_node_name,
                            "error": str(e),
                            "type": type(e).__name__
                        }
                        state["__errors__"].append(error_info)
                        print(f"[Error in merge node '{current_node_name}']: {e}")
                
                # 次のノードへ
                edge_data = self.edges.get(current_node_name)
                if edge_data is None:
                    current_node_name = self.END
                elif isinstance(edge_data, str):
                    current_node_name = edge_data
                else:
                    raise ValueError(f"Merge node '{current_node_name}' cannot have conditional or parallel edges")
                continue

            if current_node_name not in self.nodes:
                raise ValueError(f"Node '{current_node_name}' is not defined!")

            # ノード実行（エラーハンドリング追加）
            node_func = self.nodes[current_node_name]
            try:
                new_data = node_func(state)
                if new_data:
                    state.update(new_data)
            except Exception as e:
                error_info = {
                    "node": current_node_name,
                    "error": str(e),
                    "type": type(e).__name__
                }
                state["__errors__"].append(error_info)
                print(f"[Error in node '{current_node_name}']: {e}")
                # エラーが発生してもステートは更新せずに次のノードへ進む

            # --- 次の行き先を決定 ---
            edge_data = self.edges.get(current_node_name)

            if edge_data is None:
                current_node_name = self.END
            
            # 並列エッジ (List[str])
            elif isinstance(edge_data, list):
                print(f"[Parallel Execution]: Branching from '{current_node_name}' to {edge_data}")
                
                parallel_results = []
                parallel_errors = []
                
                for parallel_node in edge_data:
                    # 各ノードを独立したステートで実行
                    branch_state = copy.deepcopy(state)
                    
                    if parallel_node not in self.nodes:
                        error_msg = f"Parallel node '{parallel_node}' is not defined!"
                        error_info = {
                            "node": parallel_node,
                            "error": error_msg,
                            "type": "ValueError"
                        }
                        parallel_errors.append(error_info)
                        print(f"[Error]: {error_msg}")
                        # エラーがあっても他の並列ノードは続行
                        continue
                    
                    print(f"  → Executing parallel node: '{parallel_node}'")
                    parallel_func = self.nodes[parallel_node]
                    
                    try:
                        result = parallel_func(branch_state)
                        
                        if result:
                            branch_state.update(result)
                        
                        # 正常に実行された結果を追加
                        parallel_results.append(branch_state)
                        
                    except Exception as e:
                        error_info = {
                            "node": parallel_node,
                            "error": str(e),
                            "type": type(e).__name__,
                            "context": "parallel_execution"
                        }
                        parallel_errors.append(error_info)
                        print(f"[Error in parallel node '{parallel_node}']: {e}")
                        
                        # エラーが発生したノードの結果も記録（エラー情報付き）
                        branch_state["__node_error__"] = error_info
                        parallel_results.append(branch_state)
                
                # 並列実行のエラーをメインのエラーリストに追加
                if parallel_errors:
                    state["__errors__"].extend(parallel_errors)
                
                # 並列実行結果を一時保存（エラーがあっても全ての結果を保存）
                state["__parallel_states__"] = parallel_results
                
                # 合流ノードを探す
                merge_node = None
                for node_name, (branch_from, _) in self.merge_nodes.items():
                    if branch_from == current_node_name:
                        merge_node = node_name
                        break
                
                if merge_node:
                    current_node_name = merge_node
                else:
                    raise ValueError(f"No merge node defined for parallel edges from '{current_node_name}'")
            
            # 条件付きエッジ (Func/Key, Map)
            elif isinstance(edge_data, tuple):
                condition, path_map = edge_data
                
                if callable(condition):
                    signal = condition(state)
                elif isinstance(condition, str):
                    signal = state.get(condition)
                    if signal is None:
                        raise ValueError(f"NodeState key '{condition}' not found for routing from '{current_node_name}'")
                else:
                    raise ValueError("Invalid condition type in edge")
                
                # 文字列化（Enum対応）
                signal_str = str(signal).split('.')[-1] if hasattr(signal, 'name') else str(signal)

                # マッピング解決
                next_dest = None
                for key, val in path_map.items():
                    key_str = str(key).split('.')[-1] if hasattr(key, 'name') else str(key)
                    if key_str == signal_str:
                        next_dest = val
                        break
                
                if next_dest:
                    print(f"[Router decision]: '{signal_str}' => Go to [{next_dest}]")
                    current_node_name = next_dest
                else:
                    raise ValueError(f"Router returned '{signal}', but not found in map: {path_map}")
            
            # 固定エッジ
            else:
                current_node_name = edge_data

        return state
    
    # ==========================================================================
    # 可視化 (Mermaid) [改良版 - 並列分岐対応]
    # ==========================================================================
    def get_graph_mermaid(self) -> str:
        lines = ["graph TD"]
        
        # スタイル定義
        lines.append("  %% Styles")
        lines.append("  classDef startClass fill:#f9f,stroke:#333,stroke-width:2px,rx:10,ry:10;")
        lines.append("  classDef endClass fill:#f96,stroke:#333,stroke-width:2px,rx:10,ry:10;")
        lines.append("  classDef nodeClass fill:#e1f5fe,stroke:#0277bd,stroke-width:2px,rx:5,ry:5;")
        lines.append("  classDef routerClass fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,stroke-dasharray: 5 5,rhombus;")
        lines.append("  classDef mergeClass fill:#c8e6c9,stroke:#388e3c,stroke-width:3px,rx:5,ry:5;")
        lines.append("  classDef subStartClass fill:#eee,stroke:#999,stroke-width:1px,rx:5,ry:5;")

        def render_content(graph_obj, prefix="", is_subgraph=False):
            # START / END
            style = "subStartClass" if is_subgraph else "startClass"
            end_style = "subStartClass" if is_subgraph else "endClass"
            lines.append(f"    {prefix}{self.START}(START):::{style}")
            lines.append(f"    {prefix}{self.END}(END):::{end_style}")

            # ノード (合流ノードは特別なスタイル)
            for node in graph_obj.nodes:
                node_id = f"{prefix}{node}"
                if node in graph_obj.merge_nodes:
                    lines.append(f"    {node_id}{{{{Merge: {node}}}}}:::mergeClass")
                else:
                    lines.append(f"    {node_id}[{node}]:::nodeClass")

            # Entry Point
            if graph_obj.entry_point:
                lines.append(f"    {prefix}{self.START} --> {prefix}{graph_obj.entry_point}")

            # Edges
            for from_node, edge_data in graph_obj.edges.items():
                from_id = f"{prefix}{from_node}"
                
                # 並列エッジ
                if isinstance(edge_data, list):
                    # 並列ノードへのエッジを描画
                    for to_node in edge_data:
                        to_id = f"{prefix}{to_node}"
                        lines.append(f"    {from_id} -.parallel.-> {to_id}")
                    
                    # 並列ノードから合流ノードへのエッジを追加
                    # この from_node に対応する合流ノードを探す
                    merge_node = None
                    for m_node, (branch_from, _) in graph_obj.merge_nodes.items():
                        if branch_from == from_node:
                            merge_node = m_node
                            break
                    
                    if merge_node:
                        merge_id = f"{prefix}{merge_node}"
                        # 各並列ノードから合流ノードへの点線エッジ
                        for to_node in edge_data:
                            to_id = f"{prefix}{to_node}"
                            lines.append(f"    {to_id} -.->|merge| {merge_id}")
                
                # 条件付きエッジ
                elif isinstance(edge_data, tuple):
                    condition, path_map = edge_data
                    router_id = f"{prefix}router_{from_node}"
                    
                    if callable(condition):
                        label = f"{{{condition.__name__}}}"
                    else:
                        label = f"{{{condition}}}"
                    
                    lines.append(f"    {from_id} --> {router_id}{label}:::routerClass")
                    
                    for signal, to_node in path_map.items():
                        to_id = f"{prefix}{to_node}"
                        s_label = str(signal).split('.')[-1]
                        lines.append(f"    {router_id} -- {s_label} --> {to_id}")
                
                # 固定エッジ
                else:
                    to_id = f"{prefix}{edge_data}"
                    lines.append(f"    {from_id} --> {to_id}")

        if self.subgraphs:
            # サブグラフ描画
            for node_name, sub_graph in self.subgraphs.items():
                cluster_id = f"Sub_{node_name}"
                lines.append(f"  subgraph {cluster_id} [\"{node_name}\"]")
                lines.append("    direction TD")
                lines.append(f"    style {cluster_id} fill:#fffde7,stroke:#fbc02d,stroke-width:2px")
                render_content(sub_graph, prefix=f"{node_name}_", is_subgraph=True)
                lines.append("  end")

            # メイングラフ描画
            lines.append("  subgraph Main [Main Workflow]")
            lines.append("    style Main fill:none,stroke:none")
            render_content(self, prefix="", is_subgraph=False)
            lines.append("  end")
        
        else:
            render_content(self, prefix="", is_subgraph=False)
        
        return "\n".join(lines)
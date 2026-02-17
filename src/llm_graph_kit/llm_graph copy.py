from typing import Dict, Any, Callable, Union, Tuple, List, Set
import copy

# ステートの型定義
NodeState = Dict[str, Any]

class LLMGraph:
    """
    LangGraphスタイル: シンプルなノードとエッジでグラフを構築
    並列分岐は自動検出され、マージノードで結果が統合される
    """
    START = "__START__"
    END = "__END__"

    def __init__(self):
        self.nodes: Dict[str, Callable[[NodeState], NodeState]] = {}
        # from_node -> List[to_node] の形式でエッジを管理
        self.edges: Dict[str, List[str]] = {}
        # 条件付きエッジ: from_node -> (condition, path_map)
        self.conditional_edges: Dict[str, Tuple[Union[Callable, str], Dict[str, str]]] = {}
        self.entry_point: str = ""
        self.subgraphs: Dict[str, 'LLMGraph'] = {}

    def add_node(self, name: str, func: Callable[[NodeState], NodeState]):
        """ノードを登録"""
        self.nodes[name] = func
    
    def add_edge(self, from_node: str, to_node: str):
        """
        エッジを追加（複数のエッジを同じfrom_nodeから追加可能）
        
        Args:
            from_node: 開始ノード（STARTも使用可能）
            to_node: 終了ノード（ENDも使用可能）
        """
        if from_node == self.START:
            self.entry_point = to_node
            return
        
        if from_node not in self.edges:
            self.edges[from_node] = []
        
        self.edges[from_node].append(to_node)

    def add_conditional_edge(
        self, 
        from_node: str, 
        condition: Union[Callable[[NodeState], str], str], 
        path_map: Dict[str, str]
    ):
        """条件分岐ルートを定義"""
        self.conditional_edges[from_node] = (condition, path_map)

    def _detect_merge_nodes(self) -> Dict[str, List[str]]:
        """
        グラフ構造から自動的にマージノードを検出
        
        Returns:
            {マージノード名: [入力ノードのリスト]}
        """
        # 各ノードへの入力エッジをカウント
        incoming_edges: Dict[str, List[str]] = {}
        
        for from_node, to_nodes in self.edges.items():
            for to_node in to_nodes:
                if to_node not in incoming_edges:
                    incoming_edges[to_node] = []
                incoming_edges[to_node].append(from_node)
        
        # 複数の入力を持つノード = マージノード
        merge_nodes = {}
        for node, inputs in incoming_edges.items():
            if len(inputs) > 1:
                merge_nodes[node] = inputs
        
        return merge_nodes

    def _is_parallel_branch(self, from_node: str) -> bool:
        """指定されたノードから並列分岐しているかチェック"""
        return from_node in self.edges and len(self.edges[from_node]) > 1

    def run(self, initial_state: NodeState) -> NodeState:
        """グラフを実行"""
        if not self.entry_point:
            raise ValueError("Entry point not set. Use add_edge(LLMGraph.START, 'node_name').")

        # マージノードを検出
        merge_nodes = self._detect_merge_nodes()
        #print(f"[Detected merge nodes]: {list(merge_nodes.keys())}")

        current_node_name = self.entry_point
        state = initial_state.copy()
        state["__errors__"] = []
        
        # 並列実行の結果を追跡
        # {マージノード名: {完了した入力ノード名: ステート}}
        pending_merges: Dict[str, Dict[str, NodeState]] = {
            merge: {} for merge in merge_nodes
        }

        while current_node_name != self.END:
            if current_node_name not in self.nodes:
                raise ValueError(f"Node '{current_node_name}' is not defined!")

            #print(f"[Executing]: '{current_node_name}'")
            
            # マージノードの処理
            if current_node_name in merge_nodes:
                print(f"[Merge Node]: '{current_node_name}' - waiting for {len(merge_nodes[current_node_name])} inputs")
                
                # 全ての入力が揃っているかチェック
                required_inputs = set(merge_nodes[current_node_name])
                completed_inputs = set(pending_merges[current_node_name].keys())
                
                if not required_inputs.issubset(completed_inputs):
                    missing = required_inputs - completed_inputs
                    raise RuntimeError(
                        f"Merge node '{current_node_name}' reached before all inputs completed. "
                        f"Missing: {missing}"
                    )
                
                # 並列実行結果を取得（入力ノードの順序を保持）
                parallel_states = [
                    pending_merges[current_node_name][input_node]
                    for input_node in merge_nodes[current_node_name]
                ]
                
                # マージ関数を実行（複数のステートを引数として渡す）
                node_func = self.nodes[current_node_name]
                try:
                    # マージ関数は List[NodeState] を受け取る特殊な関数
                    merged_result = node_func(parallel_states)
                    if merged_result:
                        state.update(merged_result)
                    
                    print(f"[Merge Complete]: {len(parallel_states)} results merged")
                except Exception as e:
                    error_info = {
                        "node": current_node_name,
                        "error": str(e),
                        "type": type(e).__name__
                    }
                    state["__errors__"].append(error_info)
                    print(f"[Error in merge node '{current_node_name}']: {e}")
                
                # マージ完了後はクリーンアップ
                pending_merges[current_node_name] = {}
            
            # 通常ノードの処理
            else:
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

            # 次の行き先を決定
            # 1. 条件付きエッジをチェック
            if current_node_name in self.conditional_edges:
                condition, path_map = self.conditional_edges[current_node_name]
                
                if callable(condition):
                    signal = condition(state)
                else:
                    signal = state.get(condition)
                
                signal_str = str(signal).split('.')[-1] if hasattr(signal, 'name') else str(signal)
                
                next_dest = None
                for key, val in path_map.items():
                    key_str = str(key).split('.')[-1] if hasattr(key, 'name') else str(key)
                    if key_str == signal_str:
                        next_dest = val
                        break
                
                if next_dest:
                    print(f"[Router]: '{signal_str}' → '{next_dest}'")
                    current_node_name = next_dest
                else:
                    raise ValueError(f"Signal '{signal}' not found in path_map: {path_map}")
            
            # 2. 通常のエッジをチェック
            elif current_node_name in self.edges:
                next_nodes = self.edges[current_node_name]
                
                # 並列分岐の場合
                if len(next_nodes) > 1:
                    print(f"[Parallel Execution]: Branching to {next_nodes}")
                    
                    # 各並列ノードを独立したステートで実行
                    for parallel_node in next_nodes:
                        branch_state = copy.deepcopy(state)
                        
                        if parallel_node not in self.nodes:
                            print(f"[Error]: Node '{parallel_node}' not defined")
                            continue
                        
                        #print(f"  → Executing: '{parallel_node}'")
                        parallel_func = self.nodes[parallel_node]
                        
                        try:
                            result = parallel_func(branch_state)
                            if result:
                                branch_state.update(result)
                        except Exception as e:
                            error_info = {
                                "node": parallel_node,
                                "error": str(e),
                                "type": type(e).__name__
                            }
                            state["__errors__"].append(error_info)
                            print(f"[Error in parallel node '{parallel_node}']: {e}")
                            branch_state["__node_error__"] = error_info
                        
                        # 並列ノードの結果を保存（次のマージノード用）
                        # このノードがどのマージノードに繋がっているかを確認
                        if parallel_node in self.edges:
                            next_merge = self.edges[parallel_node][0]
                            if next_merge in merge_nodes:
                                pending_merges[next_merge][parallel_node] = branch_state
                    
                    # 並列ノードの共通の出力先（マージノード）へ移動
                    common_next = None
                    for pnode in next_nodes:
                        if pnode in self.edges:
                            pnode_next = self.edges[pnode][0]
                            if common_next is None:
                                common_next = pnode_next
                            elif common_next != pnode_next:
                                raise ValueError(
                                    f"Parallel nodes must converge to same merge node. "
                                    f"Found: {common_next} and {pnode_next}"
                                )
                    
                    if common_next:
                        current_node_name = common_next
                    else:
                        current_node_name = self.END
                
                # 単一の次ノード
                else:
                    current_node_name = next_nodes[0]
            
            # 3. エッジがない場合は終了
            else:
                current_node_name = self.END

        return state

    def get_graph_mermaid(self) -> str:
        """Mermaid図を生成"""
        lines = ["graph TD"]
        
        # スタイル定義
        lines.append("  classDef startClass fill:#f9f,stroke:#333,stroke-width:2px;")
        lines.append("  classDef endClass fill:#f96,stroke:#333,stroke-width:2px;")
        lines.append("  classDef nodeClass fill:#e1f5fe,stroke:#0277bd,stroke-width:2px;")
        lines.append("  classDef mergeClass fill:#c8e6c9,stroke:#388e3c,stroke-width:3px;")
        lines.append("  classDef routerClass fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;")

        # マージノード検出
        merge_nodes = self._detect_merge_nodes()

        # START / END
        lines.append(f"  {self.START}(START):::startClass")
        lines.append(f"  {self.END}(END):::endClass")

        # ノード描画（条件分岐ノードは菱形で表示）
        for node in self.nodes:
            if node in merge_nodes:
                # マージノード
                lines.append(f"  {node}{{{{{node}}}}}:::mergeClass")
            elif node in self.conditional_edges:
                # 条件分岐ノード（菱形）
                lines.append(f"  {node}{{{node}}}:::routerClass")
            else:
                # 通常のノード
                lines.append(f"  {node}[{node}]:::nodeClass")

        # エントリーポイント
        if self.entry_point:
            lines.append(f"  {self.START} --> {self.entry_point}")

        # 通常のエッジ
        for from_node, to_nodes in self.edges.items():
            for to_node in to_nodes:
                # 並列分岐は点線で表示
                if len(to_nodes) > 1:
                    lines.append(f"  {from_node} -.parallel.-> {to_node}")
                else:
                    lines.append(f"  {from_node} --> {to_node}")

        # 条件付きエッジ（ノードから直接分岐）
        for from_node, (condition, path_map) in self.conditional_edges.items():
            # 条件ラベルを取得
            if callable(condition):
                condition_label = f"[{condition.__name__}]"
            else:
                condition_label = f"[{condition}]"
            
            # from_nodeから直接各分岐先へエッジを描画
            for signal, to_node in path_map.items():
                signal_label = str(signal).split('.')[-1]
                lines.append(f"  {from_node} -- {signal_label} --> {to_node}")

        return "\n".join(lines)
from typing import Dict, Any, Callable, Union, Tuple

# ステートの型定義
NodeState = Dict[str, Any]

class LLMGraph:
    """
    ノードとエッジで構成されるステートマシンエンジン。
    Router関数だけでなく、ステートの値を直接参照するルーティングに対応しました。
    """
    # 定数定義
    START = "__START__"
    END = "__END__"

    def __init__(self):
        # ノード名 -> 関数
        self.nodes: Dict[str, Callable[[NodeState], NodeState]] = {}
        # ノード名 -> 次のノード名 または (条件, マッピング辞書)
        # 条件は Callable(関数) または str(ステートのキー)
        self.edges: Dict[str, Union[str, Tuple[Union[Callable, str], Dict[str, str]]]] = {} 
        self.entry_point: str = ""
        self.subgraphs: Dict[str, 'LLMGraph'] = {}

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

    def add_subgraph(self, node_name: str, subgraph: 'LLMGraph'):
        """可視化用にサブグラフを登録します"""
        self.subgraphs[node_name] = subgraph

    def run(self, initial_state: NodeState) -> NodeState:
        """グラフを実行します"""
        if not self.entry_point:
            raise ValueError("Entry point not set. Use add_edge(Graph.START, 'node_name').")

        current_node_name = self.entry_point
        state = initial_state.copy()

        while current_node_name != self.END:
            if current_node_name not in self.nodes:
                raise ValueError(f"Node '{current_node_name}' is not defined!")

            # ノード実行
            node_func = self.nodes[current_node_name]
            new_data = node_func(state)
            if new_data:
                state.update(new_data)

            # --- 次の行き先を決定 ---
            edge_data = self.edges.get(current_node_name)

            if edge_data is None:
                current_node_name = self.END
            
            # 条件付きエッジ (Func/Key, Map)
            elif isinstance(edge_data, tuple):
                condition, path_map = edge_data
                
                # ★改良点: 関数なら実行、文字列ならステートから取得
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
    # 可視化 (Mermaid) [改良版]
    # ==========================================================================
    def get_graph_mermaid(self) -> str:
        lines = ["graph TD"]
        
        # スタイル定義
        lines.append("  %% Styles")
        lines.append("  classDef startClass fill:#f9f,stroke:#333,stroke-width:2px,rx:10,ry:10;")
        lines.append("  classDef endClass fill:#f96,stroke:#333,stroke-width:2px,rx:10,ry:10;")
        lines.append("  classDef nodeClass fill:#e1f5fe,stroke:#0277bd,stroke-width:2px,rx:5,ry:5;")
        lines.append("  classDef routerClass fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,stroke-dasharray: 5 5,rhombus;")
        lines.append("  classDef subStartClass fill:#eee,stroke:#999,stroke-width:1px,rx:5,ry:5;")

        def render_content(graph_obj, prefix="", is_subgraph=False):
            # START / END
            style = "subStartClass" if is_subgraph else "startClass"
            end_style = "subStartClass" if is_subgraph else "endClass"
            lines.append(f"    {prefix}{self.START}(START):::{style}")
            lines.append(f"    {prefix}{self.END}(END):::{end_style}")

            # ノード
            for node in graph_obj.nodes:
                node_id = f"{prefix}{node}"
                lines.append(f"    {node_id}[{node}]:::nodeClass")

            # Entry Point
            if graph_obj.entry_point:
                lines.append(f"    {prefix}{self.START} --> {prefix}{graph_obj.entry_point}")

            # Edges
            for from_node, edge_data in graph_obj.edges.items():
                from_id = f"{prefix}{from_node}"
                
                if isinstance(edge_data, tuple):
                    condition, path_map = edge_data
                    router_id = f"{prefix}router_{from_node}"
                    
                    # ラベルの決定（関数名 or キー名）
                    if callable(condition):
                        label = f"{{{condition.__name__}}}"
                    else:
                        label = f"{{{condition}}}" # ステートのキー名を表示
                    
                    lines.append(f"    {from_id} --> {router_id}{label}:::routerClass")
                    
                    for signal, to_node in path_map.items():
                        to_id = f"{prefix}{to_node}"
                        s_label = str(signal).split('.')[-1]
                        lines.append(f"    {router_id} -- {s_label} --> {to_id}")
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
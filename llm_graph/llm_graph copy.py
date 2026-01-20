from typing import Dict, Any, Callable, Union, Tuple

# ステートの型定義（辞書型）
State = Dict[str, Any]

class LLMGraph:
    """
    ノードとエッジで構成されるステートマシンエンジン。
    Mermaidによるサブグラフの可視化に対応しています。
    """
    # 定数定義
    START = "__START__"
    END = "__END__"

    def __init__(self):
        # ノード名 -> 関数
        self.nodes: Dict[str, Callable[[State], State]] = {}
        # ノード名 -> 次のノード名 または (Router関数, マッピング辞書)
        self.edges: Dict[str, Union[str, Tuple[Callable, Dict[str, str]]]] = {} 
        # 開始ノード
        self.entry_point: str = ""
        
        # 【可視化用】ノード名 -> サブグラフ(Graphインスタンス)
        self.subgraphs: Dict[str, 'LLMGraph'] = {}

    def add_node(self, name: str, func: Callable[[State], State]):
        """通常のノードを登録します"""
        self.nodes[name] = func
    
    def add_node_with_subgraph(self, name: str, func: Callable[[State], State], subgraph: 'LLMGraph'):
        """
        サブグラフ構造を持つノードを登録します。
        実行関数(func)の登録に加え、可視化用にサブグラフ構造(subgraph)も保存します。
        """
        # 1. 実行用の関数を登録 (add_nodeと同じ)
        self.nodes[name] = func
        
        # 2. 可視化用の構造を登録
        self.subgraphs[name] = subgraph

    def add_edge(self, from_node: str, to_node: str):
        """
        固定ルート（A -> B）を定義します。
        Graph.START からのエッジはエントリーポイントとして扱われます。
        """
        if from_node == self.START:
            self.entry_point = to_node
            return
        self.edges[from_node] = to_node

    def add_conditional_edge(
        self, 
        from_node: str, 
        router_func: Callable[[State], str], 
        path_map: Dict[str, str]
    ):
        """
        条件分岐ルートを定義します。
        
        Args:
            from_node: 分岐元のノード名
            router_func: Stateを受け取り、シグナル(文字列)を返す関数
            path_map: { "シグナル": "行き先のノード名" } の辞書
        """
        self.edges[from_node] = (router_func, path_map)

    def add_subgraph(self, node_name: str, subgraph: 'LLMGraph'):
        """
        可視化のために、特定ノードの中身となるサブグラフを登録します。
        実際の実行ロジックには影響しません（実行はノード関数内で行われます）。
        """
        self.subgraphs[node_name] = subgraph

    def run(self, initial_state: State) -> State:
        """
        グラフを実行します。
        """
        if not self.entry_point:
            raise ValueError("Entry point not set. Use add_edge(Graph.START, 'node_name').")

        current_node_name = self.entry_point
        state = initial_state.copy()

        while current_node_name != self.END:
            if current_node_name not in self.nodes:
                raise ValueError(f"Node '{current_node_name}' is not defined!")

            # print(f"Executing: [{current_node_name}]")
            node_func = self.nodes[current_node_name]
            
            # ノード実行
            new_data = node_func(state)
            if new_data:
                state.update(new_data)

            # --- 次の行き先を決定 ---
            edge_data = self.edges.get(current_node_name)

            if edge_data is None:
                # エッジがなければ終了
                current_node_name = self.END
            
            # 条件分岐 (router_func, path_map)
            elif isinstance(edge_data, tuple):
                router_func, path_map = edge_data
                
                # ルーターを実行
                signal = router_func(state) 
                
                # Enumなどが返ってきた場合に備えて文字列化
                signal_str = str(signal).split('.')[-1] if hasattr(signal, 'name') else str(signal)

                # マッピング解決
                # 完全に一致するキーを探す（Enumのメンバ名や文字列）
                # path_mapのキーとsignal_strを比較
                next_dest = None
                for key, val in path_map.items():
                    # path_mapのキーがEnumの場合も考慮して文字列比較
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
                # print(f"  -> Next step: [{current_node_name}]")

        return state
    
    # ==========================================================================
    # 可視化 (Mermaid)
    # ==========================================================================
    def get_graph_mermaid(self) -> str:
        """
        グラフ構造をMermaid記法で出力します。
        graph TBを使用し、サブグラフ定義を先に記述することで
        メイングラフとサブグラフを並列に見やすく配置します。
        """
        lines = ["graph TB"]
        
        # --- 1. スタイル定義 ---
        lines.append("  %% Styles")
        lines.append("  classDef startClass fill:#f9f,stroke:#333,stroke-width:2px,rx:10,ry:10;")
        lines.append("  classDef endClass fill:#f96,stroke:#333,stroke-width:2px,rx:10,ry:10;")
        lines.append("  classDef nodeClass fill:#e1f5fe,stroke:#0277bd,stroke-width:2px,rx:5,ry:5;")
        lines.append("  classDef routerClass fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,stroke-dasharray: 5 5;")
        lines.append("  classDef subStartClass fill:#eee,stroke:#999,stroke-width:1px,rx:5,ry:5,font-size:10pt;")

        # --- 2. 内部描画用ヘルパー関数 ---
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

            # エッジ (START -> Entry)
            if graph_obj.entry_point:
                lines.append(f"    {prefix}{self.START} --> {prefix}{graph_obj.entry_point}")

            # エッジ (Nodes -> Nodes)
            for from_node, edge_data in graph_obj.edges.items():
                from_id = f"{prefix}{from_node}"
                
                if isinstance(edge_data, tuple):
                    # Router
                    router_func, path_map = edge_data
                    router_id = f"{prefix}router_{from_node}"
                    
                    # ルーター判定ロジックを表示
                    lines.append(f"    {from_id} -.-> {router_id}{{{router_func.__name__}}}:::routerClass")
                    
                    for signal, to_node in path_map.items():
                        to_id = f"{prefix}{to_node}"
                        label = str(signal).split('.')[-1]
                        lines.append(f"    {router_id} -- {label} --> {to_id}")
                else:
                    # Normal Edge
                    to_id = f"{prefix}{edge_data}"
                    lines.append(f"    {from_id} --> {to_id}")

        # --- 3. サブフロー (ここを先に描画) ---
        for node_name, sub_graph in self.subgraphs.items():
            sub_prefix = f"{node_name}_"
            cluster_id = f"Sub_Context_{node_name}"
            # タイトルはシンプルにノード名のみ
            title = node_name 
            
            lines.append(f"  subgraph {cluster_id} [\"{title}\"]")
            lines.append("    direction TB")
            # 薄い黄色背景 + 濃い黄色枠線
            lines.append(f"    style {cluster_id} fill:#fffde7,stroke:#fbc02d,stroke-width:2px")
            
            render_content(sub_graph, prefix=sub_prefix, is_subgraph=True)
            lines.append("  end")

        # --- 4. メインフロー (ここを後に描画) ---
        lines.append("  subgraph Main_Context [Main Workflow]")
        lines.append("    direction TB")
        # 枠線・背景・タイトル文字をすべて透明化
        lines.append("    style Main_Context fill:none,stroke:none,color:none")
        
        render_content(self, prefix="", is_subgraph=False)
        lines.append("  end")

        return "\n".join(lines)
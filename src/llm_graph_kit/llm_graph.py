from typing import Dict, Any, Callable, Union, Tuple
import copy
import inspect

# ステートの型定義
NodeState = Dict[str, Any]


class LLMGraph:
    """
    LangGraphスタイル: シンプルなノードとエッジでグラフを構築する。
    逐次実行と条件分岐に対応する。
    """
    START = "__START__"
    END = "__END__"

    def __init__(self):
        self.nodes: Dict[str, Callable[[NodeState], NodeState]] = {}
        # from_node -> to_node の単一エッジ
        self.edges: Dict[str, str] = {}
        # 条件付きエッジ: from_node -> (condition, path_map)
        self.conditional_edges: Dict[str, Tuple[Union[Callable, str], Dict[str, str]]] = {}
        self.entry_point: str = ""

    def add_node(self, name: str, func: Callable[[NodeState], NodeState]):
        """ノードを登録"""
        if name in (self.START, self.END):
            raise ValueError(
                f"Node name '{name}' is reserved. "
                f"Use a different name for the node."
            )
        self.nodes[name] = func

    def add_edge(self, from_node: str, to_node: str):
        """
        エッジを追加する。1つの from_node からは1本だけ登録可能。
        分岐させたい場合は add_conditional_edge を使う。

        Args:
            from_node: 開始ノード（STARTも使用可能）
            to_node: 終了ノード（ENDも使用可能）
        """
        if from_node == self.END:
            raise ValueError(
                f"Cannot add an edge from '{self.END}'. "
                f"END is a terminal node."
            )

        if from_node == self.START:
            self.entry_point = to_node
            return

        if from_node in self.edges:
            raise ValueError(
                f"Edge from '{from_node}' already exists. "
                f"Use add_conditional_edge for branching."
            )
        self.edges[from_node] = to_node

    def add_conditional_edge(
        self,
        from_node: str,
        condition: Union[Callable[[NodeState], str], str],
        path_map: Dict[str, str],
    ):
        """条件分岐ルートを定義"""
        self.conditional_edges[from_node] = (condition, path_map)

    def run(self, initial_state: NodeState):
        """グラフを実行（ジェネレータとして動作）"""
        if not self.entry_point:
            raise ValueError("Entry point not set.")

        # ノードを呼び出し、ジェネレータならイベントを上位へ流し、
        # return 値（または通常の戻り値）を最終結果として返す共通処理
        def execute_node_logic(func, arg):
            response = func(arg)
            if inspect.isgenerator(response):
                return (yield from response)
            return response

        # 呼び出し側のオブジェクトに副作用を残さないよう、ネスト構造ごと複製する
        state = copy.deepcopy(initial_state)
        state.setdefault("__errors__", [])
        current_node_name = self.entry_point

        while current_node_name != self.END:
            if current_node_name not in self.nodes:
                raise ValueError(f"Node '{current_node_name}' is not defined!")

            node_func = self.nodes[current_node_name]
            try:
                result = yield from execute_node_logic(node_func, state)
                if result:
                    state.update(result)
            except Exception as e:
                state["__errors__"].append(f"Error in {current_node_name}: {str(e)}")
                yield {"type": "error", "agent": current_node_name, "content": str(e)}

            # 次の行き先を決定
            if current_node_name in self.conditional_edges:
                condition, path_map = self.conditional_edges[current_node_name]
                signal = condition(state) if callable(condition) else state.get(condition)
                signal_str = str(signal).split('.')[-1] if hasattr(signal, 'name') else str(signal)

                next_dest = None
                for key, val in path_map.items():
                    if str(key) == signal_str:
                        next_dest = val
                        break
                current_node_name = next_dest if next_dest else self.END

            elif current_node_name in self.edges:
                current_node_name = self.edges[current_node_name]
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
        lines.append("  classDef routerClass fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;")

        # START / END
        lines.append(f"  {self.START}(START):::startClass")
        lines.append(f"  {self.END}(END):::endClass")

        # ノード描画（条件分岐は菱形、通常は矩形）
        for node in self.nodes:
            if node in self.conditional_edges:
                lines.append(f"  {node}{{{node}}}:::routerClass")
            else:
                lines.append(f"  {node}[{node}]:::nodeClass")

        # エントリーポイント
        if self.entry_point:
            lines.append(f"  {self.START} --> {self.entry_point}")

        # 通常のエッジ
        for from_node, to_node in self.edges.items():
            lines.append(f"  {from_node} --> {to_node}")

        # 条件付きエッジ
        for from_node, (condition, path_map) in self.conditional_edges.items():
            for signal, to_node in path_map.items():
                signal_label = str(signal).split('.')[-1]
                lines.append(f"  {from_node} -- {signal_label} --> {to_node}")

        return "\n".join(lines)

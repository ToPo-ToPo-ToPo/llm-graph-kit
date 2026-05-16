from typing import Dict, Any, Callable, Union, Tuple, Type, Optional, FrozenSet, Set
import copy
import inspect

# ステートの型定義
NodeState = Dict[str, Any]


class LLMGraph:
    """
    LangGraphスタイル: シンプルなノードとエッジでグラフを構築する。
    逐次実行と条件分岐に対応する。

    state_schema を渡すと、ステートに書き込めるキーを宣言キーに限定できる。
    宣言にないキーが initial_state / ノード戻り値 / 条件分岐の condition で
    現れた場合は、即座に ValueError を送出する。

    例:
        from typing import TypedDict, Optional

        class MyState(TypedDict, total=False):
            input: str
            plan: Optional[str]

        g = LLMGraph(state_schema=MyState)

    state_schema=None の場合は宣言なしのフリーフォーム dict として動作する。
    """

    START = "__START__"
    END = "__END__"
    # ライブラリが管理する予約キー。ユーザーは書き込めない
    RESERVED_STATE_KEYS: FrozenSet[str] = frozenset({"__errors__"})

    def __init__(self, state_schema: Optional[Type] = None):
        self.nodes: Dict[str, Callable[[NodeState], NodeState]] = {}
        # from_node -> to_node の単一エッジ
        self.edges: Dict[str, str] = {}
        # 条件付きエッジ: from_node -> (condition, path_map)
        self.conditional_edges: Dict[str, Tuple[Union[Callable, str], Dict[str, str]]] = {}
        self.entry_point: str = ""
        self.state_schema = state_schema

        # スキーマから許可キーを解決
        self._allowed_keys: Optional[Set[str]] = None
        if state_schema is not None:
            declared = set(getattr(state_schema, "__annotations__", {}).keys())
            if not declared:
                raise ValueError(
                    f"state_schema {state_schema!r} has no fields. "
                    f"Use a TypedDict (or class with __annotations__) "
                    f"with at least one field."
                )
            conflict = declared & self.RESERVED_STATE_KEYS
            if conflict:
                raise ValueError(
                    f"state_schema declares reserved key(s) {sorted(conflict)}. "
                    f"These are managed by the library and cannot be declared."
                )
            self._allowed_keys = declared | self.RESERVED_STATE_KEYS

    # ------------------------------------------------------------------
    # スキーマ検証ヘルパー
    # ------------------------------------------------------------------
    def _check_reserved_write(self, mapping: NodeState, context: str) -> None:
        """ユーザーが予約キーへ書き込もうとしていないか検査"""
        forbidden = set(mapping.keys()) & self.RESERVED_STATE_KEYS
        if forbidden:
            raise ValueError(
                f"Cannot write to reserved state key(s) {sorted(forbidden)} "
                f"from {context}. These are managed by the library."
            )

    def _check_schema(self, mapping: NodeState, context: str) -> None:
        """スキーマ宣言時のみ、未宣言キーを検出する"""
        if self._allowed_keys is None:
            return
        unknown = set(mapping.keys()) - self._allowed_keys
        if unknown:
            declared = sorted(self._allowed_keys - self.RESERVED_STATE_KEYS)
            raise ValueError(
                f"Unknown state key(s) {sorted(unknown)} in {context}. "
                f"Declared keys: {declared}"
            )

    # ------------------------------------------------------------------
    # 構築
    # ------------------------------------------------------------------
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
        # condition に文字列を渡している場合、スキーマと整合するか検査
        if (
            self._allowed_keys is not None
            and isinstance(condition, str)
            and condition not in self._allowed_keys
        ):
            declared = sorted(self._allowed_keys - self.RESERVED_STATE_KEYS)
            raise ValueError(
                f"Conditional edge condition key '{condition}' is not in state_schema. "
                f"Declared keys: {declared}"
            )
        self.conditional_edges[from_node] = (condition, path_map)

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------
    def run(self, initial_state: NodeState):
        """グラフを実行（ジェネレータとして動作）"""
        if not self.entry_point:
            raise ValueError("Entry point not set.")

        # 初期 state も検査対象（予約キー書き込み禁止 + スキーマ整合）
        self._check_reserved_write(initial_state, "initial_state")
        self._check_schema(initial_state, "initial_state")

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
            result = None
            try:
                result = yield from execute_node_logic(node_func, state)
            except Exception as e:
                state["__errors__"].append(f"Error in {current_node_name}: {str(e)}")
                yield {"type": "error", "agent": current_node_name, "content": str(e)}

            # スキーマ違反は捕捉せず即時に例外を伝播させる
            if result:
                context = f"node '{current_node_name}' return value"
                self._check_reserved_write(result, context)
                self._check_schema(result, context)
                state.update(result)

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

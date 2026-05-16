from typing import Dict, Any, Callable, Union, Tuple, Type, Optional, FrozenSet, Set
import copy
import inspect
import warnings

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

        # state_schema は将来必須になる予定。スキーマなしの構築は非推奨
        if state_schema is None:
            warnings.warn(
                "Creating LLMGraph without state_schema is deprecated and will "
                "be removed in a future version. Declare a TypedDict (or any "
                "class with __annotations__) and pass it as state_schema to "
                "enable runtime key validation and static type checking. "
                "See example_with_schema.py.",
                DeprecationWarning,
                stacklevel=2,
            )

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
        if name in self.nodes:
            raise ValueError(
                f"Node '{name}' is already registered. "
                f"Each node must have a unique name."
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
            if self.entry_point:
                raise ValueError(
                    f"Entry point is already set to '{self.entry_point}'. "
                    f"Cannot reassign START -> '{to_node}'."
                )
            self.entry_point = to_node
            return

        if from_node in self.conditional_edges:
            raise ValueError(
                f"Node '{from_node}' already has a conditional edge. "
                f"A node cannot have both a regular edge and a conditional edge."
            )
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

        if from_node in self.conditional_edges:
            raise ValueError(
                f"Conditional edge from '{from_node}' already exists. "
                f"Each node can have at most one conditional edge."
            )
        if from_node in self.edges:
            raise ValueError(
                f"Node '{from_node}' already has a regular edge. "
                f"A node cannot have both a regular edge and a conditional edge."
            )
        self.conditional_edges[from_node] = (condition, path_map)

    # ------------------------------------------------------------------
    # 実行前検証
    # ------------------------------------------------------------------
    def _validate_graph(self) -> None:
        """run() 開始前にグラフ構造の整合性を検証する。

        - entry_point が登録済みノードを指していること
        - edges / conditional_edges の遷移先がすべて登録済みノードか END であること
        """
        valid_targets = set(self.nodes.keys()) | {self.END}

        if self.entry_point not in valid_targets:
            raise ValueError(
                f"Entry point '{self.entry_point}' is not a registered node. "
                f"Registered: {sorted(self.nodes.keys())}"
            )

        for from_node, to_node in self.edges.items():
            if from_node not in self.nodes:
                raise ValueError(
                    f"Edge source '{from_node}' is not a registered node. "
                    f"Registered: {sorted(self.nodes.keys())}"
                )
            if to_node not in valid_targets:
                raise ValueError(
                    f"Edge target '{to_node}' (from '{from_node}') is not a "
                    f"registered node. Registered: {sorted(self.nodes.keys())}"
                )

        for from_node, (_condition, path_map) in self.conditional_edges.items():
            if from_node not in self.nodes:
                raise ValueError(
                    f"Conditional edge source '{from_node}' is not a registered node. "
                    f"Registered: {sorted(self.nodes.keys())}"
                )
            for signal, to_node in path_map.items():
                if to_node not in valid_targets:
                    raise ValueError(
                        f"Conditional edge target '{to_node}' (from '{from_node}', "
                        f"signal '{signal}') is not a registered node. "
                        f"Registered: {sorted(self.nodes.keys())}"
                    )

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------
    def run(self, initial_state: NodeState, max_steps: int = 100):
        """グラフを実行（ジェネレータとして動作）

        Args:
            initial_state: 初期ステート。
            max_steps: 実行するノード数の上限。サイクルによる無限ループを防止する。
                超えた場合は RuntimeError を送出する。
        """
        if not self.entry_point:
            raise ValueError("Entry point not set.")
        if not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError(f"max_steps must be a positive int, got {max_steps!r}.")

        # 構造（ノード参照）を実行前に一括検証
        self._validate_graph()

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
        step = 0

        while current_node_name != self.END:
            # 無限ループ防止: 1 ノード実行を 1 ステップとしてカウント
            if step >= max_steps:
                raise RuntimeError(
                    f"Graph execution exceeded max_steps={max_steps} at node "
                    f"'{current_node_name}'. Possible infinite loop. "
                    f"Pass a larger max_steps to run() if legitimate."
                )
            step += 1

            node_func = self.nodes[current_node_name]
            result = None
            try:
                result = yield from execute_node_logic(node_func, state)
            except Exception as e:
                state["__errors__"].append(f"Error in {current_node_name}: {str(e)}")
                yield {"type": "error", "agent": current_node_name, "content": str(e)}

            # 戻り値の型と内容を検証（スキーマ違反は即時に伝播）
            if result is not None:
                if not isinstance(result, dict):
                    raise TypeError(
                        f"Node '{current_node_name}' must return a dict or None, "
                        f"got {type(result).__name__}."
                    )
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

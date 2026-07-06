"""GUI で組み立てたグラフ仕様から LLMGraph を構築する。

フロントエンドの JSON 仕様（state_schema / nodes / edges）を受け取り、
ユーザーが入力した Python コードをコンパイルしてノード関数として登録する。

セキュリティ注意:
    ノードの本体はユーザーが入力した任意の Python コードを exec で評価する。
    GUI は基本的に開発者がローカルで使うことを想定しており、Web 公開する
    用途には適さない。launch() のデフォルトも 127.0.0.1 にバインドしている。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict
import textwrap

from ..llm_graph import LLMGraph, NodeState


# ---------------------------------------------------------------------------
# 仕様 (frontend からの JSON)
# ---------------------------------------------------------------------------
# state_schema: [{"name": "input", "type": "str"}, ...]
# nodes: [
#   {"id": "...", "kind": "function"|"conditional", "name": "...", "code": "...", ...}
# ]
# edges: [
#   {"source": "...", "target": "...", "signal": null|"..."}
# ]
# ---------------------------------------------------------------------------

# Drawflow 側で使う「START」「END」を表す論理ノード ID
START_ID = "__start__"
END_ID = "__end__"

# 許可する state field 型名 -> 実際の Python 型
_TYPE_MAP: Dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "Any": object,
}


def build_state_schema(fields: List[Dict[str, str]]) -> Optional[type]:
    """state_schema の field 定義から TypedDict を動的生成する。

    fields が空なら None を返す（schema なしで動作）。
    """
    if not fields:
        return None

    annotations: Dict[str, type] = {}
    for f in fields:
        name = f.get("name", "").strip()
        type_name = f.get("type", "Any").strip() or "Any"
        if not name:
            continue
        if type_name not in _TYPE_MAP:
            raise ValueError(
                f"Unknown state field type '{type_name}'. "
                f"Allowed: {sorted(_TYPE_MAP.keys())}"
            )
        annotations[name] = _TYPE_MAP[type_name]

    if not annotations:
        return None

    return TypedDict("State", annotations, total=False)  # type: ignore[operator]


def _compile_function(code: str, expected_name: str) -> Any:
    """ユーザー入力のコードから関数オブジェクトを取り出す。

    code は通常 `def expected_name(state): ...` の形だが、コードブロックに
    複数の def や import を含んでも構わない。最終的に expected_name の名前で
    定義された呼び出し可能オブジェクトを返す。
    """
    if not code or not code.strip():
        raise ValueError(f"Node '{expected_name}' has empty code.")

    namespace: Dict[str, Any] = {}
    # state ヒント用に NodeState を import 可能にしておく
    namespace["NodeState"] = NodeState

    try:
        compiled = compile(textwrap.dedent(code), f"<gui:{expected_name}>", "exec")
        exec(compiled, namespace)  # noqa: S102 - ユーザーコード評価は本機能の目的
    except SyntaxError as e:
        raise ValueError(
            f"Syntax error in node '{expected_name}': {e.msg} (line {e.lineno})"
        ) from e

    func = namespace.get(expected_name)
    if func is None or not callable(func):
        # 同名関数がなければ、最後に定義された callable を拾うフォールバック
        callables = [
            v for k, v in namespace.items()
            if callable(v) and not k.startswith("_") and k != "NodeState"
        ]
        if not callables:
            raise ValueError(
                f"Node '{expected_name}' code must define a function "
                f"(e.g. `def {expected_name}(state): ...`)."
            )
        func = callables[-1]
    return func


def _compile_condition(code: str, node_name: str):
    """conditional ノードの condition コードを評価して callable を返す。

    code は `lambda state: ...` か `def fn(state): ...` のいずれか。
    """
    code = textwrap.dedent(code).strip()
    if not code:
        raise ValueError(f"Conditional node '{node_name}' has empty condition.")

    namespace: Dict[str, Any] = {}
    # lambda 形式をそのまま eval する
    if code.startswith("lambda"):
        try:
            return eval(code, namespace)  # noqa: S307
        except SyntaxError as e:
            raise ValueError(
                f"Syntax error in condition for '{node_name}': {e.msg}"
            ) from e

    # def 形式: exec して呼び出し可能を取り出す
    try:
        compiled = compile(code, f"<gui-cond:{node_name}>", "exec")
        exec(compiled, namespace)  # noqa: S102
    except SyntaxError as e:
        raise ValueError(
            f"Syntax error in condition for '{node_name}': {e.msg} (line {e.lineno})"
        ) from e
    callables = [
        v for k, v in namespace.items()
        if callable(v) and not k.startswith("_")
    ]
    if not callables:
        raise ValueError(
            f"Condition code for '{node_name}' must define a function or be a lambda."
        )
    return callables[-1]


def build_graph_from_spec(spec: Dict[str, Any]) -> LLMGraph:
    """フロントエンドからの仕様を LLMGraph に変換する。"""
    state_schema = build_state_schema(spec.get("state_schema") or [])
    nodes_spec: List[Dict[str, Any]] = spec.get("nodes") or []
    edges_spec: List[Dict[str, Any]] = spec.get("edges") or []

    graph = LLMGraph(state_schema=state_schema)

    # ID -> ノード仕様の辞書
    by_id = {n["id"]: n for n in nodes_spec}

    # 各ノードに使う「名前」(LLMGraph 内のユニーク識別子) を決定
    # 同名のものがあれば後ろの id を採用するわけにいかないので、ここで一意性チェック
    used_names: Dict[str, str] = {}  # name -> id
    for n in nodes_spec:
        if n["kind"] in ("start", "end"):
            continue
        name = (n.get("name") or "").strip()
        if not name:
            raise ValueError(f"Node {n['id']} has empty name.")
        if name in used_names and used_names[name] != n["id"]:
            raise ValueError(
                f"Duplicate node name '{name}'. Each node must have a unique name."
            )
        used_names[name] = n["id"]

    def name_of(node_id: str) -> str:
        if node_id == START_ID:
            return LLMGraph.START
        if node_id == END_ID:
            return LLMGraph.END
        spec = by_id.get(node_id)
        if spec is None:
            raise ValueError(f"Edge references unknown node id '{node_id}'.")
        return spec["name"].strip()

    # ノード登録 (function / conditional のみ)
    for n in nodes_spec:
        if n["kind"] == "function":
            func = _compile_function(n.get("code", ""), n["name"].strip())
            graph.add_node(n["name"].strip(), func)
        elif n["kind"] == "conditional":
            # conditional は関数本体としても登録する必要がある。
            # 既に decision を state に入れているなら、何もせず state を素通しでよい。
            # シンプルに「pass する関数」を入れ、condition は別途 add_conditional_edge で渡す。
            code = n.get("code", "").strip()
            if code:
                func = _compile_function(code, n["name"].strip())
            else:
                def _passthrough(state: NodeState):  # type: ignore[no-redef]
                    return None
                func = _passthrough
            graph.add_node(n["name"].strip(), func)
        elif n["kind"] in ("start", "end"):
            continue
        else:
            raise ValueError(f"Unknown node kind '{n['kind']}' (id={n['id']}).")

    # エッジ登録
    # conditional は signal -> target の path_map を集約する必要があるため、
    # 一旦 conditional 出力エッジを別バケットに集める
    cond_paths: Dict[str, Dict[str, str]] = {}

    for e in edges_spec:
        src_id = e["source"]
        dst_id = e["target"]
        signal = e.get("signal")

        # source が conditional ノードのときは path_map に積む
        if src_id in by_id and by_id[src_id]["kind"] == "conditional":
            src_name = by_id[src_id]["name"].strip()
            if not signal:
                raise ValueError(
                    f"Edge from conditional node '{src_name}' must have a signal "
                    f"(label on the output port)."
                )
            cond_paths.setdefault(src_name, {})[signal] = name_of(dst_id)
        else:
            graph.add_edge(name_of(src_id), name_of(dst_id))

    # conditional の condition を取り出して add_conditional_edge
    for n in nodes_spec:
        if n["kind"] != "conditional":
            continue
        src_name = n["name"].strip()
        path_map = cond_paths.get(src_name, {})
        if not path_map:
            raise ValueError(
                f"Conditional node '{src_name}' has no outgoing edges. "
                f"Add at least one labelled output."
            )
        cond_kind = (n.get("condition_kind") or "key").strip()
        cond_value = n.get("condition_value", "").strip()
        if not cond_value:
            raise ValueError(
                f"Conditional node '{src_name}' has empty condition_value."
            )
        condition: Any
        if cond_kind == "key":
            condition = cond_value
        elif cond_kind == "code":
            condition = _compile_condition(cond_value, src_name)
        else:
            raise ValueError(
                f"Unknown condition_kind '{cond_kind}' for node '{src_name}'. "
                f"Use 'key' or 'code'."
            )
        graph.add_conditional_edge(src_name, condition, path_map)

    return graph


def parse_initial_state(spec: Dict[str, Any]) -> NodeState:
    """フロントエンドから受け取った initial_state を dict として返す。

    JSON は既に dict にデコードされている前提。
    """
    initial = spec.get("initial_state") or {}
    if not isinstance(initial, dict):
        raise ValueError(
            f"initial_state must be an object, got {type(initial).__name__}."
        )
    return initial

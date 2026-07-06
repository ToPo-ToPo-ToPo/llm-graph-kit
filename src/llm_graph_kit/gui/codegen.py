"""GUI 仕様から Python ソースコードを生成する。"""
from __future__ import annotations

from typing import Any, Dict, List
import textwrap

from .builder import START_ID, END_ID


def _indent(code: str, spaces: int = 4) -> str:
    """テキストブロックを spaces 分インデント。"""
    pad = " " * spaces
    return "\n".join(pad + line if line else "" for line in code.splitlines())


def _sanitize_id(name: str) -> str:
    """Python 識別子として使える文字列に整形。"""
    out = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
    if not out or out[0].isdigit():
        out = "n_" + out
    return out


def generate_python_code(spec: Dict[str, Any]) -> str:
    """フロント仕様から実行可能な Python スクリプトを生成する。"""
    state_fields: List[Dict[str, str]] = spec.get("state_schema") or []
    nodes_spec: List[Dict[str, Any]] = spec.get("nodes") or []
    edges_spec: List[Dict[str, Any]] = spec.get("edges") or []
    initial_state: Dict[str, Any] = spec.get("initial_state") or {}

    lines: List[str] = []
    lines.append('"""GUI で組み立てたグラフから自動生成された Python コード。"""')
    lines.append("from typing import TypedDict, Any, List, Dict, Optional")
    lines.append("")
    lines.append("from llm_graph_kit import LLMGraph, NodeState")
    lines.append("")
    lines.append("")

    # ---- State schema ----
    if state_fields:
        lines.append("class State(TypedDict, total=False):")
        type_alias = {
            "str": "str", "int": "int", "float": "float", "bool": "bool",
            "list": "List[Any]", "dict": "Dict[str, Any]", "Any": "Any",
        }
        for f in state_fields:
            name = (f.get("name") or "").strip()
            tname = (f.get("type") or "Any").strip() or "Any"
            if not name:
                continue
            lines.append(f"    {name}: {type_alias.get(tname, 'Any')}")
        lines.append("")
        lines.append("")
    else:
        lines.append("# state_schema 未設定: フリーフォーム dict として動作")
        lines.append("State = None  # type: ignore[assignment]")
        lines.append("")
        lines.append("")

    # ---- Node functions ----
    by_id = {n["id"]: n for n in nodes_spec}
    for n in nodes_spec:
        if n["kind"] == "function":
            lines.append(f"# --- node: {n['name']} ---")
            lines.append(textwrap.dedent(n.get("code", "")).rstrip())
            lines.append("")
            lines.append("")
        elif n["kind"] == "conditional":
            code = n.get("code", "").strip()
            if code:
                lines.append(f"# --- conditional node: {n['name']} ---")
                lines.append(textwrap.dedent(code).rstrip())
                lines.append("")
                lines.append("")
            # condition が code 形式なら関数として書き出す
            if (n.get("condition_kind") or "key") == "code":
                cv = (n.get("condition_value") or "").strip()
                if cv and not cv.startswith("lambda"):
                    lines.append(f"# --- condition for: {n['name']} ---")
                    lines.append(textwrap.dedent(cv).rstrip())
                    lines.append("")
                    lines.append("")

    # ---- build_graph() ----
    lines.append("def build_graph() -> LLMGraph:")
    if state_fields:
        lines.append("    g = LLMGraph(state_schema=State)")
    else:
        lines.append("    g = LLMGraph()")
    lines.append("")

    for n in nodes_spec:
        if n["kind"] in ("function", "conditional"):
            name = n["name"].strip()
            # function 名はユーザーコードに含まれる識別子。code 内の def 名を取得。
            func_ref = name
            if n["kind"] == "conditional" and not n.get("code", "").strip():
                # 本体コードのない conditional は素通しの passthrough を登録する。
                # コメントは add_node(...) の外側に置く（式の途中に # を挟むと
                # 閉じ括弧がコメントに飲まれて SyntaxError になる）。
                func_ref = "lambda state: None"
                lines.append(
                    f"    g.add_node({name!r}, {func_ref})  # passthrough for {name!r}"
                )
            else:
                lines.append(f"    g.add_node({name!r}, {func_ref})")
    lines.append("")

    # edges (regular)
    cond_paths: Dict[str, Dict[str, str]] = {}
    for e in edges_spec:
        src_id = e["source"]
        dst_id = e["target"]
        signal = e.get("signal")

        def resolve(node_id: str) -> str:
            if node_id == START_ID:
                return "LLMGraph.START"
            if node_id == END_ID:
                return "LLMGraph.END"
            return repr(by_id[node_id]["name"].strip())

        if src_id in by_id and by_id[src_id]["kind"] == "conditional":
            src_name = by_id[src_id]["name"].strip()
            cond_paths.setdefault(src_name, {})[signal] = resolve(dst_id)
        else:
            lines.append(f"    g.add_edge({resolve(src_id)}, {resolve(dst_id)})")
    lines.append("")

    # conditional edges
    for n in nodes_spec:
        if n["kind"] != "conditional":
            continue
        name = n["name"].strip()
        path_map = cond_paths.get(name, {})
        cond_kind = (n.get("condition_kind") or "key").strip()
        cv = (n.get("condition_value") or "").strip()
        if cond_kind == "key":
            cond_repr = repr(cv)
        else:
            if cv.startswith("lambda"):
                cond_repr = cv
            else:
                # def 形式なので関数名を取得
                cond_repr = _extract_def_name(cv) or "None"
        # path_map を整形
        pm_lines = ["{"]
        for sig, target in path_map.items():
            pm_lines.append(f"        {sig!r}: {target},")
        pm_lines.append("    }")
        pm_str = "\n".join(pm_lines)
        lines.append(
            f"    g.add_conditional_edge({name!r}, {cond_repr}, {pm_str})"
        )
    lines.append("")
    lines.append("    return g")
    lines.append("")
    lines.append("")

    # ---- main ----
    lines.append("def main():")
    lines.append("    g = build_graph()")
    lines.append("    print(g.get_graph_mermaid())")
    lines.append("")
    if initial_state:
        lines.append(f"    initial_state = {initial_state!r}")
    else:
        lines.append("    initial_state = {}")
    lines.append("    gen = g.run(initial_state)")
    lines.append("    final_state = None")
    lines.append("    try:")
    lines.append("        while True:")
    lines.append("            event = next(gen)")
    lines.append("            print('event:', event)")
    lines.append("    except StopIteration as e:")
    lines.append("        final_state = e.value")
    lines.append("    print('final:', final_state)")
    lines.append("")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    main()")
    lines.append("")

    return "\n".join(lines)


def _extract_def_name(code: str) -> str:
    """`def name(...):` 形式のコードから関数名を取り出す。"""
    code = textwrap.dedent(code).strip()
    if not code.startswith("def "):
        return ""
    rest = code[4:].lstrip()
    name = ""
    for ch in rest:
        if ch.isalnum() or ch == "_":
            name += ch
        else:
            break
    return name

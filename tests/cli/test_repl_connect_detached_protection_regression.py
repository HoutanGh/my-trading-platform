from __future__ import annotations

import ast
from pathlib import Path


def _repl_source() -> str:
    path = Path(__file__).resolve().parents[2] / "apps" / "cli" / "repl.py"
    return path.read_text(encoding="utf-8")


def test_connect_runs_detached_protection_reconcile_after_orphan_reconcile() -> None:
    tree = ast.parse(_repl_source())
    cmd_connect: ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "REPL":
            for class_node in node.body:
                if isinstance(class_node, ast.AsyncFunctionDef) and class_node.name == "_cmd_connect":
                    cmd_connect = class_node
                    break
    assert cmd_connect is not None

    call_names: list[str] = []
    for node in ast.walk(cmd_connect):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Attribute):
            call_names.append(func.attr)

    orphan_idx = call_names.index("_reconcile_orphan_exit_orders")
    detached_idx = call_names.index("_reconcile_detached_protection_coverage")
    assert detached_idx > orphan_idx

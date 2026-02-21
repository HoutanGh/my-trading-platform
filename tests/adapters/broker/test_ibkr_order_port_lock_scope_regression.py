from __future__ import annotations

import ast
from pathlib import Path

def _order_port_source_path() -> Path:
    return Path(__file__).resolve().parents[3] / "apps" / "adapters" / "broker" / "ibkr_order_port.py"


class _LockScopedCancelVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.offenders: list[tuple[int, str]] = []
        self._state_lock_depth = 0
        self._function_stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_With(self, node: ast.With) -> None:
        holds_state_lock = any(
            isinstance(item.context_expr, ast.Name) and item.context_expr.id == "state_lock"
            for item in node.items
        )
        if holds_state_lock:
            self._state_lock_depth += 1
        self.generic_visit(node)
        if holds_state_lock:
            self._state_lock_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        if self._state_lock_depth > 0 and _is_cancel_call(node):
            context = "->".join(self._function_stack) if self._function_stack else "<module>"
            self.offenders.append((node.lineno, context))
        self.generic_visit(node)


def _is_cancel_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "_safe_cancel_order"
    if isinstance(func, ast.Attribute):
        return func.attr == "cancelOrder"
    return False


def _find_lock_scoped_cancel_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    visitor = _LockScopedCancelVisitor()
    visitor.visit(tree)
    return visitor.offenders


def test_detached_callbacks_do_not_call_cancel_while_holding_state_lock() -> None:
    source = _order_port_source_path().read_text(encoding="utf-8")
    offenders = _find_lock_scoped_cancel_calls(source)
    assert offenders == [], f"lock-scoped cancel calls found at: {offenders}"

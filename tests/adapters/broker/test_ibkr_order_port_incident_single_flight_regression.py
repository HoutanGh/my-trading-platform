from __future__ import annotations

import ast
from pathlib import Path


def _order_port_source_path() -> Path:
    return Path(__file__).resolve().parents[3] / "apps" / "adapters" / "broker" / "ibkr_order_port.py"


def _order_port_source() -> str:
    return _order_port_source_path().read_text(encoding="utf-8")


class _IncidentSchedulerVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._function_stack: list[str] = []
        self.direct_managed_incident_contexts: list[tuple[str, ...]] = []
        self.schedule_child_incident_contexts: list[tuple[str, ...]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _is_managed_incident_schedule_call(node):
            self.direct_managed_incident_contexts.append(tuple(self._function_stack))
        if isinstance(node.func, ast.Name) and node.func.id == "_schedule_child_incident":
            self.schedule_child_incident_contexts.append(tuple(self._function_stack))
        self.generic_visit(node)


def _is_managed_incident_schedule_call(node: ast.Call) -> bool:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "_schedule_managed_coroutine"):
        return False
    if len(node.args) < 2:
        return False
    maybe_incident_call = node.args[1]
    if not isinstance(maybe_incident_call, ast.Call):
        return False
    inner_func = maybe_incident_call.func
    return isinstance(inner_func, ast.Name) and inner_func.id == "_handle_child_incident"


def _visit_incident_scheduler_nodes() -> _IncidentSchedulerVisitor:
    tree = ast.parse(_order_port_source())
    visitor = _IncidentSchedulerVisitor()
    visitor.visit(tree)
    return visitor


def test_managed_incident_scheduling_is_wrapped_by_single_flight_helper() -> None:
    visitor = _visit_incident_scheduler_nodes()
    contexts = {"->".join(context) for context in visitor.direct_managed_incident_contexts}
    assert contexts == {
        "_submit_ladder_order_detached_3_pairs->_schedule_child_incident",
        "_submit_ladder_order_detached_70_30->_schedule_child_incident",
    }


def test_detached_callbacks_route_incidents_through_single_flight_helper() -> None:
    visitor = _visit_incident_scheduler_nodes()
    contexts = {"->".join(context) for context in visitor.schedule_child_incident_contexts}
    expected = {
        "_submit_ladder_order_detached_70_30->_reprice_pair2_stop",
        "_submit_ladder_order_detached_70_30->_on_gateway_message",
        "_submit_ladder_order_detached_70_30->_on_tp_status",
        "_submit_ladder_order_detached_70_30->_on_stop_status",
        "_submit_ladder_order_detached_3_pairs->_reprice_single_pair_stop",
        "_submit_ladder_order_detached_3_pairs->_on_gateway_message",
        "_submit_ladder_order_detached_3_pairs->_on_tp_status",
        "_submit_ladder_order_detached_3_pairs->_on_stop_status",
    }
    assert contexts.issuperset(expected), f"missing incident helper callsites: {sorted(expected - contexts)}"


def test_detached_flows_define_inflight_incident_gate() -> None:
    source = _order_port_source()
    assert source.count("incident_pairs_inflight: set[int] = set()") == 2
    assert source.count("incident_pairs_active=incident_pairs_active | incident_pairs_inflight") == 2

from __future__ import annotations

from datetime import datetime, timezone

import apps.cli.event_printer as event_printer
from apps.cli.event_printer import _should_hide_gateway_log, print_event
from apps.core.ops.events import (
    DetachedSessionRestoreCompleted,
    DetachedSessionRestored,
    IbGatewayLog,
)


def _gateway_event(*, code: int, message: str, req_id: int = 1) -> IbGatewayLog:
    return IbGatewayLog(
        code=code,
        message=message,
        req_id=req_id,
        timestamp=datetime.now(timezone.utc),
    )


def test_should_hide_gateway_log_hides_162_query_cancel() -> None:
    event = _gateway_event(
        code=162,
        message="Historical Market Data Service error message:API historical data query cancelled: 123",
    )

    assert _should_hide_gateway_log(event) is True


def test_should_hide_gateway_log_hides_2150_invalid_position_trade_value() -> None:
    event = _gateway_event(
        code=2150,
        message="Invalid position trade derived value",
    )

    assert _should_hide_gateway_log(event) is True


def test_should_hide_gateway_log_keeps_other_2150_messages_visible() -> None:
    event = _gateway_event(
        code=2150,
        message="Different broker error",
    )

    assert _should_hide_gateway_log(event) is False


def test_should_hide_gateway_log_hides_generic_202_status() -> None:
    event = _gateway_event(
        code=202,
        message="Order Canceled - reason:",
        req_id=1702,
    )

    assert _should_hide_gateway_log(event) is True


def test_print_event_keeps_correlated_202_for_tracked_order() -> None:
    event_printer._ORDER_STATE_BY_ID.clear()
    event_printer._ORDER_STATE_BY_ID[1702] = event_printer._OrderLifecycleState(
        symbol="AAPL",
        leg="tp1",
        stage="submitted",
        target_qty=100.0,
    )
    event = _gateway_event(
        code=202,
        message="Order Canceled - reason:",
        req_id=1702,
    )
    printed: list[tuple[str, str]] = []

    original_print_line = event_printer._print_line
    try:
        event_printer._print_line = lambda _ts, label, message: printed.append((label, message))
        assert print_event(event) is True
    finally:
        event_printer._print_line = original_print_line
        event_printer._ORDER_STATE_BY_ID.clear()

    assert printed
    assert printed[0][0] == "OrderInfo"


def test_print_event_reports_detached_session_restored() -> None:
    event = DetachedSessionRestored.now(
        trigger="connection_established",
        scope="all_clients",
        account="DU1",
        symbol="AAPL",
        client_tag="breakout:AAPL:10",
        execution_mode="detached70",
        state="protected",
        reason="reconnect_restored",
        position_qty=100.0,
        protected_qty=100.0,
        uncovered_qty=0.0,
        active_take_profit_order_ids=[10, 11],
        active_stop_order_ids=[12],
        primary_stop_order_id=12,
    )
    printed: list[tuple[str, str]] = []

    original_print_line = event_printer._print_line
    try:
        event_printer._print_line = lambda _ts, label, message: printed.append((label, message))
        assert print_event(event) is True
    finally:
        event_printer._print_line = original_print_line

    assert printed
    assert printed[0][0] == "DetachedSessionRestored"


def test_print_event_hides_empty_detached_session_restore_summary() -> None:
    event = DetachedSessionRestoreCompleted.now(
        trigger="connection_established",
        scope="all_clients",
        active_order_count=0,
        position_count=0,
        inspected_position_count=0,
        restored_count=0,
        protected_count=0,
        unprotected_count=0,
    )

    assert print_event(event) is False

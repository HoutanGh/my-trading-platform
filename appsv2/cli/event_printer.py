from __future__ import annotations

from appsv2.core.orders.events import (
    OrderIdAssigned,
    OrderIntent,
    OrderSent,
    OrderStatusChanged,
)


def print_event(event: object) -> None:
    if isinstance(event, OrderIntent):
        _print_line(
            event.timestamp,
            "OrderIntent",
            f"{event.spec.side.value} {event.spec.symbol} qty={event.spec.qty}",
        )
        return
    if isinstance(event, OrderSent):
        _print_line(
            event.timestamp,
            "OrderSent",
            f"{event.spec.side.value} {event.spec.symbol} qty={event.spec.qty}",
        )
        return
    if isinstance(event, OrderIdAssigned):
        _print_line(
            event.timestamp,
            "OrderIdAssigned",
            f"{event.spec.symbol} order_id={event.order_id}",
        )
        return
    if isinstance(event, OrderStatusChanged):
        _print_line(
            event.timestamp,
            "OrderStatus",
            f"{event.spec.symbol} order_id={event.order_id} status={event.status}",
        )
        return
    _print_line(None, "Event", repr(event))


def _print_line(timestamp, label: str, message: str) -> None:
    if timestamp:
        ts = timestamp.isoformat()
        print(f"[{ts}] {label}: {message}")
    else:
        print(f"{label}: {message}")

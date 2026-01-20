from __future__ import annotations

from apps.core.orders.events import (
    OrderIdAssigned,
    OrderIntent,
    OrderSent,
    OrderStatusChanged,
    OrderFilled,
)
from apps.core.pnl.events import (
    PnlIngestFailed,
    PnlIngestFinished,
    PnlIngestStarted,
)
from apps.core.strategies.breakout.events import (
    BreakoutBreakDetected,
    BreakoutConfirmed,
    BreakoutRejected,
    BreakoutStarted,
    BreakoutStopped,
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
    if isinstance(event, OrderFilled):
        _print_line(
            event.timestamp,
            "OrderFilled",
            (
                f"{event.spec.symbol} order_id={event.order_id} status={event.status} "
                f"filled={event.filled_qty} avg_price={event.avg_fill_price} "
                f"remaining={event.remaining_qty}"
            ),
        )
        return
    if isinstance(event, PnlIngestStarted):
        _print_line(
            event.timestamp,
            "PnlIngestStarted",
            f"{event.account} csv={event.csv_path}",
        )
        return
    if isinstance(event, PnlIngestFinished):
        result = event.result
        _print_line(
            event.timestamp,
            "PnlIngestFinished",
            f"{result.account} days={result.days_ingested} rows={result.rows_used}",
        )
        return
    if isinstance(event, PnlIngestFailed):
        _print_line(
            event.timestamp,
            "PnlIngestFailed",
            f"{event.account} error={event.error}",
        )
        return
    if isinstance(event, BreakoutStarted):
        _print_line(
            event.timestamp,
            "BreakoutStarted",
            f"{event.symbol} level={event.rule.level}",
        )
        return
    if isinstance(event, BreakoutBreakDetected):
        _print_line(
            event.timestamp,
            "BreakoutBreak",
            f"{event.symbol} level={event.level} bar={event.bar.timestamp.isoformat()}",
        )
        return
    if isinstance(event, BreakoutConfirmed):
        extras = []
        if event.take_profit is not None:
            extras.append(f"tp={event.take_profit}")
        if event.stop_loss is not None:
            extras.append(f"sl={event.stop_loss}")
        suffix = f" {' '.join(extras)}" if extras else ""
        _print_line(
            event.timestamp,
            "BreakoutConfirmed",
            f"{event.symbol} level={event.level} bar={event.bar.timestamp.isoformat()}{suffix}",
        )
        return
    if isinstance(event, BreakoutRejected):
        _print_line(
            event.timestamp,
            "BreakoutRejected",
            f"{event.symbol} level={event.level} reason={event.reason}",
        )
        return
    if isinstance(event, BreakoutStopped):
        reason = event.reason or "-"
        _print_line(
            event.timestamp,
            "BreakoutStopped",
            f"{event.symbol} reason={reason}",
        )
        return
    _print_line(None, "Event", repr(event))


def _print_line(timestamp, label: str, message: str) -> None:
    if timestamp:
        ts = timestamp.isoformat()
        print(f"[{ts}] {label}: {message}")
    else:
        print(f"{label}: {message}")


def make_prompting_event_printer(prompt: str):
    def _handler(event: object) -> None:
        print_event(event)
        print(prompt, end="", flush=True)

    return _handler

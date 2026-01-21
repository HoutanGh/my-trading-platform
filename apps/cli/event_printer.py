from __future__ import annotations

from typing import Optional

from apps.core.orders.events import BracketChildOrderFilled, OrderFilled
from apps.core.strategies.breakout.events import (
    BreakoutBreakDetected,
    BreakoutConfirmed,
    BreakoutStarted,
)


_PROMPT_PREFIX: Optional[str] = None


def print_event(event: object) -> bool:
    if isinstance(event, BreakoutStarted):
        _print_line(
            event.timestamp,
            "BreakoutStarted",
            f"{event.symbol} level={event.rule.level}",
        )
        return True
    if isinstance(event, BreakoutBreakDetected):
        bar_time = _format_time(event.bar.timestamp)
        _print_line(
            event.timestamp,
            "BreakoutBreak",
            f"{event.symbol} level={event.level} bar={bar_time}",
        )
        return True
    if isinstance(event, BreakoutConfirmed):
        extras = []
        if event.take_profit is not None:
            extras.append(f"tp={event.take_profit}")
        if event.stop_loss is not None:
            extras.append(f"sl={event.stop_loss}")
        suffix = f" {' '.join(extras)}" if extras else ""
        bar_time = _format_time(event.bar.timestamp)
        _print_line(
            event.timestamp,
            "BreakoutConfirmed",
            f"{event.symbol} level={event.level} bar={bar_time}{suffix}",
        )
        return True
    if isinstance(event, OrderFilled):
        if not _is_fill_event(event.status, event.filled_qty):
            return False
        _print_line(
            event.timestamp,
            "OrderFilled",
            (
                f"{event.spec.symbol} order_id={event.order_id} status={event.status} "
                f"filled={event.filled_qty} avg_price={event.avg_fill_price} "
                f"remaining={event.remaining_qty}"
            ),
        )
        return True
    if isinstance(event, BracketChildOrderFilled):
        if not _is_fill_event(event.status, event.filled_qty):
            return False
        label = "TakeProfitFilled" if event.kind == "take_profit" else "StopLossFilled"
        fill_price = event.avg_fill_price if event.avg_fill_price is not None else event.price
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} qty={event.filled_qty or event.qty} "
                f"price={fill_price}"
            ),
        )
        return True
    return False


def _print_line(timestamp, label: str, message: str) -> None:
    prefix = f"{_PROMPT_PREFIX} " if _PROMPT_PREFIX else ""
    if timestamp:
        ts = _format_time(timestamp)
        print(f"{prefix}[{ts}] {label}: {message}")
    else:
        print(f"{prefix}{label}: {message}")


def _format_time(timestamp) -> str:
    return timestamp.strftime("%H:%M:%S")


def _is_fill_event(status: Optional[str], filled_qty: Optional[float]) -> bool:
    if filled_qty is not None and filled_qty > 0:
        return True
    if not status:
        return False
    normalized = str(status).strip().lower()
    return normalized in {"filled", "partiallyfilled", "partially_filled"}


def make_prompting_event_printer(prompt: str):
    _set_prompt_prefix(prompt)

    def _handler(event: object) -> None:
        if print_event(event):
            print(prompt, end="", flush=True)

    return _handler


def _set_prompt_prefix(prompt: str) -> None:
    global _PROMPT_PREFIX
    _PROMPT_PREFIX = prompt.strip()

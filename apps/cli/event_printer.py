from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apps.core.orders.events import BracketChildOrderFilled, OrderFilled
from apps.core.ops.events import IbGatewayLog
from apps.core.strategies.breakout.events import (
    BreakoutBreakDetected,
    BreakoutConfirmed,
    BreakoutFastTriggered,
    BreakoutStarted,
    BreakoutStopped,
)


_PROMPT_PREFIX: Optional[str] = None
_CONFIRMED_BY_TAG: dict[str, "_EntryFillTiming"] = {}
_MAX_GATEWAY_MSG_LEN = 160


@dataclass
class _EntryFillTiming:
    confirmed_at: datetime
    partial_reported: bool = False


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
    if isinstance(event, BreakoutFastTriggered):
        bar_time = _format_time(event.bar.timestamp)
        thresholds = event.thresholds
        _print_line(
            event.timestamp,
            "BreakoutFast",
            (
                f"{event.symbol} level={event.level} bar={bar_time} "
                f"elapsed={thresholds.elapsed_seconds}s "
                f"dist={thresholds.distance_cents}c "
                f"range={thresholds.spread_cents}c "
                f"max_range={thresholds.max_spread_cents}c "
                f"scale={thresholds.price_scale:g}"
            ),
        )
        return True
    if isinstance(event, BreakoutConfirmed):
        if event.client_tag:
            _CONFIRMED_BY_TAG[event.client_tag] = _EntryFillTiming(
                confirmed_at=_normalize_timestamp(event.timestamp)
            )
        extras = []
        if event.take_profits:
            extras.append(f"tp={_format_tp_list(event.take_profits)}")
        elif event.take_profit is not None:
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
    if isinstance(event, BreakoutStopped):
        if event.client_tag and event.reason not in {"order_submitted", "order_submitted_fast"}:
            _CONFIRMED_BY_TAG.pop(event.client_tag, None)
        return False
    if isinstance(event, IbGatewayLog):
        if event.code is None and not event.message:
            return False
        label = _gateway_label(event)
        parts = []
        if event.code is not None:
            parts.append(f"code={event.code}")
        if event.req_id is not None:
            parts.append(f"req_id={event.req_id}")
        if event.message:
            parts.append(f"msg={_shorten_message(event.message)}")
        if event.advanced:
            parts.append("advanced=1")
        parts.append("see=ib_gateway.jsonl")
        _print_line(event.timestamp, label, " ".join(parts))
        return True
    if isinstance(event, OrderFilled):
        if not _is_fill_event(event.status, event.filled_qty):
            return False
        latency_suffix = _entry_fill_latency_suffix(event)
        _print_line(
            event.timestamp,
            "OrderFilled",
            (
                f"{event.spec.symbol} order_id={event.order_id} status={event.status} "
                f"filled={event.filled_qty} avg_price={event.avg_fill_price} "
                f"remaining={event.remaining_qty}{latency_suffix}"
            ),
        )
        return True
    if isinstance(event, BracketChildOrderFilled):
        if not _is_fill_event(event.status, event.filled_qty):
            return False
        if event.kind.startswith("take_profit"):
            suffix = event.kind.split("_", 2)[-1] if event.kind.startswith("take_profit_") else ""
            label = f"TakeProfit{suffix}Filled" if suffix else "TakeProfitFilled"
        else:
            label = "StopLossFilled"
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


def _format_tp_list(levels: list[float]) -> str:
    return "[" + ",".join(f"{level:g}" for level in levels) + "]"


def _shorten_message(message: str, max_len: int = _MAX_GATEWAY_MSG_LEN) -> str:
    compact = " ".join(str(message).split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


def _gateway_label(event: IbGatewayLog) -> str:
    message = str(event.message or "").lower()
    if "is ok" in message:
        return "IbStatus"
    if "inactive" in message or "broken" in message:
        return "IbWarn"
    return "IbError"


def _print_line(timestamp, label: str, message: str) -> None:
    prefix = f"{_PROMPT_PREFIX} " if _PROMPT_PREFIX else ""
    if timestamp:
        ts = _format_time(timestamp)
        print(f"{prefix}[{ts}] {label}: {message}")
    else:
        print(f"{prefix}{label}: {message}")


def _format_time(timestamp) -> str:
    if getattr(timestamp, "tzinfo", None) is None:
        return timestamp.strftime("%H:%M:%S.%f")
    offset = timestamp.strftime("%z")
    if offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{timestamp.strftime('%H:%M:%S.%f')}{offset}"


def _is_fill_event(status: Optional[str], filled_qty: Optional[float]) -> bool:
    if filled_qty is not None and filled_qty > 0:
        return True
    if not status:
        return False
    normalized = str(status).strip().lower()
    return normalized in {"filled", "partiallyfilled", "partially_filled"}


def _entry_fill_latency_suffix(event: OrderFilled) -> str:
    client_tag = event.spec.client_tag
    if not client_tag:
        return ""
    timing = _CONFIRMED_BY_TAG.get(client_tag)
    if not timing:
        return ""
    extras = []
    if (event.filled_qty or 0) > 0 and not timing.partial_reported:
        extras.append(f"partial_latency={_format_latency(timing.confirmed_at, event.timestamp)}")
        timing.partial_reported = True
    if _is_full_fill(event):
        extras.append(f"full_latency={_format_latency(timing.confirmed_at, event.timestamp)}")
        _CONFIRMED_BY_TAG.pop(client_tag, None)
    if not extras:
        return ""
    return " " + " ".join(extras)


def _is_full_fill(event: OrderFilled) -> bool:
    if event.status:
        normalized = str(event.status).strip().lower()
        if normalized == "filled":
            return True
    if event.filled_qty is None:
        return False
    return event.filled_qty >= event.spec.qty


def _format_latency(start: datetime, end: datetime) -> str:
    start_ts = _normalize_timestamp(start)
    end_ts = _normalize_timestamp(end)
    delta = (end_ts - start_ts).total_seconds()
    if delta < 0:
        delta = 0.0
    return f"{delta:.6f}s"


def _normalize_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def make_prompting_event_printer(prompt: str):
    _set_prompt_prefix(prompt)

    def _handler(event: object) -> None:
        if print_event(event):
            print(prompt, end="", flush=True)

    return _handler


def _set_prompt_prefix(prompt: str) -> None:
    global _PROMPT_PREFIX
    _PROMPT_PREFIX = prompt.strip()

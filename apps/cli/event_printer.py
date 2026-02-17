from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
import sys
from typing import Optional

try:
    import readline
except ImportError:
    readline = None

from apps.core.orders.events import (
    BracketChildOrderFilled,
    BracketChildOrderBrokerSnapshot,
    BracketChildQuantityMismatchDetected,
    LadderProtectionStateChanged,
    LadderStopLossCancelled,
    LadderStopLossReplaceFailed,
    LadderStopLossReplaced,
    OrderFilled,
)
from apps.core.ops.events import (
    BarStreamCompetingSessionBlocked,
    BarStreamCompetingSessionCleared,
    BarStreamRecovered,
    BarStreamRecoveryFailed,
    BarStreamRecoveryScanScheduled,
    BarStreamRecoveryStarted,
    BarStreamStalled,
    IbGatewayLog,
    OrphanExitOrderCancelFailed,
    OrphanExitOrderCancelled,
    OrphanExitOrderDetected,
    OrphanExitReconciliationCompleted,
)
from apps.core.strategies.breakout.events import (
    BreakoutBreakDetected,
    BreakoutConfirmed,
    BreakoutFastTriggered,
    BreakoutRejected,
    BreakoutStarted,
    BreakoutStopped,
    BreakoutTakeProfitsUpdated,
)


_PROMPT_PREFIX: Optional[str] = None
_CONFIRMED_BY_TAG: dict[str, "_EntryFillTiming"] = {}
_MAX_GATEWAY_MSG_LEN = 160
_MAX_GATEWAY_PREVIEW_LEN = 72
_MAX_GATEWAY_ERROR_PREVIEW_LEN = 140
_BAR_STREAM_INFO_COOLDOWN_SECONDS = 15.0
_BAR_STREAM_INFO_LAST_PRINTED: dict[tuple[str, str, str, bool], float] = {}
_SUPPRESSED_GATEWAY_REQ_IDS: dict[int, float] = {}
_GATEWAY_REQ_SUPPRESS_TTL_SECONDS = 10.0
_GATEWAY_CODE_ALIASES = {
    10197: "competing_session",
    2104: "md_ok",
    2107: "hmds_inactive",
    2106: "hmds_ok",
    2158: "secdef_ok",
    1102: "restored",
}
_GATEWAY_CODE_SHORT_MESSAGES = {
    10197: "competing live session",
    2104: "market data farm ok",
    2107: "historical data farm inactive",
    2106: "historical data farm ok",
    2158: "sec-def data farm ok",
    1102: "connectivity restored",
}


@dataclass
class _EntryFillTiming:
    confirmed_at: datetime
    partial_reported: bool = False


def print_event(event: object) -> bool:
    if isinstance(event, BreakoutStarted):
        suffix = _breakout_tp_sl_suffix(
            take_profit=event.take_profit,
            take_profits=event.take_profits,
            stop_loss=event.stop_loss,
        )
        _print_line(
            event.timestamp,
            "BreakoutStarted",
            f"{event.symbol} level={event.rule.level}{suffix}",
        )
        return True
    if isinstance(event, BreakoutBreakDetected):
        bar_time = _format_time(event.bar.timestamp)
        suffix = _breakout_tp_sl_suffix(
            take_profit=event.take_profit,
            take_profits=event.take_profits,
            stop_loss=event.stop_loss,
        )
        _print_line(
            event.timestamp,
            "BreakoutBreak",
            f"{event.symbol} level={event.level} bar={bar_time}{suffix}",
        )
        return True
    if isinstance(event, BreakoutFastTriggered):
        bar_time = _format_time(event.bar.timestamp)
        thresholds = event.thresholds
        suffix = _breakout_tp_sl_suffix(
            take_profit=event.take_profit,
            take_profits=event.take_profits,
            stop_loss=event.stop_loss,
        )
        _print_line(
            event.timestamp,
            "BreakoutFast",
            (
                f"{event.symbol} level={event.level} bar={bar_time} "
                f"elapsed={thresholds.elapsed_seconds}s "
                f"dist={thresholds.distance_cents}c "
                f"range={thresholds.spread_cents}c "
                f"max_range={thresholds.max_spread_cents}c "
                f"scale={thresholds.price_scale:g}{suffix}"
            ),
        )
        return True
    if isinstance(event, BreakoutConfirmed):
        if event.client_tag:
            _CONFIRMED_BY_TAG[event.client_tag] = _EntryFillTiming(
                confirmed_at=_normalize_timestamp(event.timestamp)
            )
        suffix = _breakout_tp_sl_suffix(
            take_profit=event.take_profit,
            take_profits=event.take_profits,
            stop_loss=event.stop_loss,
        )
        bar_time = _format_time(event.bar.timestamp)
        _print_line(
            event.timestamp,
            "BreakoutConfirmed",
            f"{event.symbol} level={event.level} bar={bar_time}{suffix}",
        )
        return True
    if isinstance(event, BreakoutRejected):
        bar_time = _format_time(event.bar.timestamp)
        extras = []
        if event.reason:
            extras.append(f"reason={event.reason}")
        if event.reason == "quote_stale":
            if event.quote_age_seconds is not None:
                extras.append(f"age={event.quote_age_seconds:.3f}s")
            if event.quote_max_age_seconds is not None:
                extras.append(f"max={event.quote_max_age_seconds:.3f}s")
        extras.extend(
            _breakout_tp_sl_parts(
                take_profit=event.take_profit,
                take_profits=event.take_profits,
                stop_loss=event.stop_loss,
            )
        )
        suffix = f" {' '.join(extras)}" if extras else ""
        _print_line(
            event.timestamp,
            "BreakoutRejected",
            f"{event.symbol} level={event.level} bar={bar_time}{suffix}",
        )
        return True
    if isinstance(event, BreakoutStopped):
        if event.client_tag and event.reason not in {"order_submitted", "order_submitted_fast"}:
            _CONFIRMED_BY_TAG.pop(event.client_tag, None)
        return False
    if isinstance(event, BreakoutTakeProfitsUpdated):
        parts = [f"tp={_format_tp_list(event.take_profits)}"]
        if event.take_profit_qtys:
            qty_text = ",".join(str(item) for item in event.take_profit_qtys)
            parts.append(f"qtys=[{qty_text}]")
        if event.stop_loss is not None:
            parts.append(f"sl={event.stop_loss:g}")
        if event.source:
            parts.append(f"source={event.source}")
        _print_line(
            event.timestamp,
            "BreakoutTpUpdated",
            f"{event.symbol} {' '.join(parts)}",
        )
        return True
    if isinstance(event, IbGatewayLog):
        if _should_hide_gateway_log(event):
            return False
        if event.code is None and not event.message:
            return False
        label = _gateway_label(event)
        parts = []
        message = _gateway_message_for_display(event)
        if message:
            max_len = _gateway_message_preview_len(label)
            parts.append(_shorten_message(message, max_len=max_len))
        if event.code is not None:
            parts.append(_format_gateway_code(event.code))
        if event.req_id is not None and event.req_id >= 0:
            parts.append(f"req={event.req_id}")
        if event.advanced:
            parts.append("adv")
        _print_line(event.timestamp, label, " ".join(parts))
        return True
    if isinstance(event, BarStreamStalled):
        _print_line(
            event.timestamp,
            "BarStreamStalled",
            (
                f"{event.symbol} bar={event.bar_size} use_rth={event.use_rth} "
                f"silence={event.silence_seconds:.1f}s timeout={event.timeout_seconds:.1f}s"
            ),
        )
        return True
    if isinstance(event, BarStreamRecovered):
        if not _should_print_bar_stream_info(
            kind="recovered",
            symbol=event.symbol,
            bar_size=event.bar_size,
            use_rth=event.use_rth,
        ):
            return False
        _print_line(
            event.timestamp,
            "BarStreamRecovered",
            (
                f"{event.symbol} bar={event.bar_size} use_rth={event.use_rth} "
                f"downtime={event.downtime_seconds:.1f}s"
            ),
        )
        return True
    if isinstance(event, BarStreamRecoveryStarted):
        if not _should_print_bar_stream_info(
            kind="heal",
            symbol=event.symbol,
            bar_size=event.bar_size,
            use_rth=event.use_rth,
        ):
            return False
        _print_line(
            event.timestamp,
            "BarStreamHeal",
            (
                f"{event.symbol} bar={event.bar_size} use_rth={event.use_rth} "
                f"attempt={event.attempt}"
            ),
        )
        return True
    if isinstance(event, BarStreamRecoveryFailed):
        _print_line(
            event.timestamp,
            "BarStreamHealFail",
            (
                f"{event.symbol} bar={event.bar_size} use_rth={event.use_rth} "
                f"attempt={event.attempt} retry_in={event.retry_in_seconds:.1f}s "
                f"msg={_shorten_message(event.message)}"
            ),
        )
        return True
    if isinstance(event, BarStreamCompetingSessionBlocked):
        _print_line(
            event.timestamp,
            "BarStreamBlocked",
            (
                f"{event.symbol} bar={event.bar_size} use_rth={event.use_rth} "
                f"code={event.code} msg={_shorten_message(event.message)}"
            ),
        )
        return True
    if isinstance(event, BarStreamCompetingSessionCleared):
        _print_line(
            event.timestamp,
            "BarStreamUnblocked",
            (
                f"{event.symbol} bar={event.bar_size} use_rth={event.use_rth} "
                f"code={event.code} msg={_shorten_message(event.message)}"
            ),
        )
        return True
    if isinstance(event, BarStreamRecoveryScanScheduled):
        _print_line(
            event.timestamp,
            "BarStreamScan",
            f"reason={event.reason} groups={event.groups} streams={event.streams}",
        )
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
    if isinstance(event, BracketChildOrderBrokerSnapshot):
        broker_qty = "-"
        if event.broker_order_qty is not None:
            broker_qty = f"{event.broker_order_qty:g}"
        if event.kind.startswith("det70_"):
            label = "Det70ChildSnapshot"
        elif event.kind.startswith("detached_"):
            label = "DetachedChildSnapshot"
        else:
            label = "ChildOrderSnapshot"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} kind={event.kind} order_id={event.order_id} parent={event.parent_order_id} "
                f"expected={event.expected_qty:g} broker={broker_qty} status={event.status or '-'}"
            ),
        )
        return True
    if isinstance(event, BracketChildQuantityMismatchDetected):
        if event.kind.startswith("det70_"):
            label = "Det70QtyMismatch"
        elif event.kind.startswith("detached_"):
            label = "DetachedQtyMismatch"
        else:
            label = "ChildQtyMismatch"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} kind={event.kind} order_id={event.order_id} parent={event.parent_order_id} "
                f"expected={event.expected_qty:g} broker={event.broker_order_qty:g} "
                f"status={event.status or '-'}"
            ),
        )
        return True
    if isinstance(event, BracketChildOrderFilled):
        if not _is_fill_event(event.status, event.filled_qty):
            return False
        if event.kind.startswith("det70_tp_"):
            suffix = event.kind.split("_")[-1]
            label = f"Det70TakeProfit{suffix}Filled"
        elif event.kind == "det70_stop":
            label = "Det70StopLossFilled"
        elif event.kind.startswith("detached_tp_"):
            suffix = event.kind.split("_")[-1]
            label = f"DetachedTakeProfit{suffix}Filled"
        elif event.kind == "detached_stop":
            label = "DetachedStopLossFilled"
        elif event.kind.startswith("take_profit"):
            suffix = event.kind.split("_", 2)[-1] if event.kind.startswith("take_profit_") else ""
            label = f"TakeProfit{suffix}Filled" if suffix else "TakeProfitFilled"
        else:
            label = "StopLossFilled"
        fill_price = event.avg_fill_price if event.avg_fill_price is not None else event.price
        broker_suffix = ""
        if event.expected_qty is not None and event.broker_order_qty is not None:
            if abs(event.expected_qty - event.broker_order_qty) > 1e-9:
                broker_suffix = (
                    f" QTY_MISMATCH expected={event.expected_qty:g} broker={event.broker_order_qty:g}"
                )
            else:
                broker_suffix = f" expected={event.expected_qty:g} broker={event.broker_order_qty:g}"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} qty={event.filled_qty or event.qty} "
                f"price={fill_price}{broker_suffix}"
            ),
        )
        return True
    if isinstance(event, LadderStopLossReplaced):
        if event.execution_mode == "detached70":
            label = "Det70StopLossReplaced"
        elif event.execution_mode == "detached":
            label = "DetachedStopLossReplaced"
        else:
            label = "StopLossReplaced"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} reason={event.reason} "
                f"order={event.old_order_id}->{event.new_order_id} "
                f"qty={event.old_qty}->{event.new_qty} "
                f"price={event.old_price:g}->{event.new_price:g}"
            ),
        )
        return True
    if isinstance(event, LadderStopLossReplaceFailed):
        broker = []
        if event.broker_code is not None:
            broker.append(f"code={event.broker_code}")
        if event.broker_message:
            broker.append(f"msg={_shorten_message(event.broker_message)}")
        broker_suffix = f" {' '.join(broker)}" if broker else ""
        if event.execution_mode == "detached70":
            label = "Det70StopLossReplaceFailed"
        elif event.execution_mode == "detached":
            label = "DetachedStopLossReplaceFailed"
        else:
            label = "StopLossReplaceFailed"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} order_id={event.old_order_id} "
                f"attempt_qty={event.attempted_qty} attempt_price={event.attempted_price:g} "
                f"status={event.status or '-'}{broker_suffix}"
            ),
        )
        return True
    if isinstance(event, LadderProtectionStateChanged):
        tp_ids = ",".join(str(order_id) for order_id in event.active_take_profit_order_ids)
        if event.execution_mode == "detached70":
            label = "Det70StopProtection"
        elif event.execution_mode == "detached":
            label = "DetachedStopProtection"
        else:
            label = "StopProtection"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} state={event.state} reason={event.reason} "
                f"stop_order_id={event.stop_order_id} active_tp_ids=[{tp_ids}]"
            ),
        )
        return True
    if isinstance(event, LadderStopLossCancelled):
        if event.execution_mode == "detached70":
            label = "Det70StopLossCancelled"
        elif event.execution_mode == "detached":
            label = "DetachedStopLossCancelled"
        else:
            label = "StopLossCancelled"
        _print_line(
            event.timestamp,
            label,
            (
                f"{event.symbol} reason={event.reason} "
                f"order_id={event.order_id} qty={event.qty} price={event.price:g}"
            ),
        )
        return True
    if isinstance(event, OrphanExitOrderDetected):
        _print_line(
            event.timestamp,
            "OrphanExitDetected",
            (
                f"{event.symbol} order_id={event.order_id} parent={event.parent_order_id} "
                f"remaining={event.remaining_qty} status={event.status or '-'} "
                f"action={event.action} trigger={event.trigger} scope={event.scope}"
            ),
        )
        return True
    if isinstance(event, OrphanExitOrderCancelled):
        _print_line(
            event.timestamp,
            "OrphanExitCancelled",
            (
                f"{event.symbol} order_id={event.order_id} "
                f"status={event.status or '-'} trigger={event.trigger}"
            ),
        )
        return True
    if isinstance(event, OrphanExitOrderCancelFailed):
        _print_line(
            event.timestamp,
            "OrphanExitCancelFailed",
            (
                f"{event.symbol} order_id={event.order_id} "
                f"error={event.error_type} msg={_shorten_message(event.message)}"
            ),
        )
        return True
    if isinstance(event, OrphanExitReconciliationCompleted):
        if event.orphan_count <= 0:
            return False
        _print_line(
            event.timestamp,
            "OrphanExitRecon",
            (
                f"{event.trigger} {event.scope}/{event.action} "
                f"active={event.active_order_count} pos={event.position_count} "
                f"orphan={event.orphan_count} cancelled={event.cancelled_count} "
                f"failed={event.cancel_failed_count}"
            ),
        )
        return True
    return False


def _format_tp_list(levels: list[float]) -> str:
    return "[" + ",".join(f"{level:g}" for level in levels) + "]"


def _breakout_tp_sl_parts(
    *,
    take_profit: Optional[float],
    take_profits: Optional[list[float]],
    stop_loss: Optional[float],
) -> list[str]:
    parts = []
    if take_profits:
        parts.append(f"tp={_format_tp_list(take_profits)}")
    elif take_profit is not None:
        parts.append(f"tp={take_profit}")
    if stop_loss is not None:
        parts.append(f"sl={stop_loss}")
    return parts


def _breakout_tp_sl_suffix(
    *,
    take_profit: Optional[float],
    take_profits: Optional[list[float]],
    stop_loss: Optional[float],
) -> str:
    parts = _breakout_tp_sl_parts(
        take_profit=take_profit,
        take_profits=take_profits,
        stop_loss=stop_loss,
    )
    return f" {' '.join(parts)}" if parts else ""


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


def _should_hide_gateway_log(event: IbGatewayLog) -> bool:
    if _is_gateway_req_suppressed(event.req_id):
        return True
    if event.code != 162:
        return False
    message = str(event.message or "").lower()
    return "query cancel" in message


def _format_gateway_code(code: int) -> str:
    alias = _GATEWAY_CODE_ALIASES.get(code)
    if not alias:
        return str(code)
    return f"{code}/{alias}"


def _gateway_message_for_display(event: IbGatewayLog) -> str:
    code = event.code
    if code in _GATEWAY_CODE_SHORT_MESSAGES:
        return _GATEWAY_CODE_SHORT_MESSAGES[code]
    return " ".join(str(event.message or "").split())


def _gateway_message_preview_len(label: str) -> int:
    if label == "IbError":
        return _MAX_GATEWAY_ERROR_PREVIEW_LEN
    return _MAX_GATEWAY_PREVIEW_LEN


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


def _should_print_bar_stream_info(
    *,
    kind: str,
    symbol: str,
    bar_size: str,
    use_rth: bool,
) -> bool:
    if _BAR_STREAM_INFO_COOLDOWN_SECONDS <= 0:
        return True
    key = (kind, symbol.upper(), bar_size, use_rth)
    now = monotonic()
    last = _BAR_STREAM_INFO_LAST_PRINTED.get(key)
    if last is not None and (now - last) < _BAR_STREAM_INFO_COOLDOWN_SECONDS:
        return False
    _BAR_STREAM_INFO_LAST_PRINTED[key] = now
    return True


def make_prompting_event_printer(prompt: str):
    # Avoid repeating the prompt prefix on event lines; we redraw the prompt below.
    _set_prompt_prefix("")

    def _handler(event: object) -> None:
        buffer = ""
        if readline is not None:
            try:
                buffer = readline.get_line_buffer()
            except Exception:
                buffer = ""
            # Clear the current input line before printing async output.
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()
        printed = print_event(event)
        if readline is not None:
            # Redraw the prompt and any partially typed input.
            sys.stdout.write(prompt + buffer)
            sys.stdout.flush()
            return
        if printed:
            print(prompt, end="", flush=True)

    return _handler


def _set_prompt_prefix(prompt: str) -> None:
    global _PROMPT_PREFIX
    _PROMPT_PREFIX = prompt.strip()


def suppress_gateway_req_id(
    req_id: int,
    *,
    ttl_seconds: float = _GATEWAY_REQ_SUPPRESS_TTL_SECONDS,
) -> None:
    if req_id < 0:
        return
    ttl = ttl_seconds if ttl_seconds > 0 else _GATEWAY_REQ_SUPPRESS_TTL_SECONDS
    now = monotonic()
    _SUPPRESSED_GATEWAY_REQ_IDS[req_id] = now + ttl
    _prune_suppressed_gateway_req_ids(now)


def _is_gateway_req_suppressed(req_id: Optional[int]) -> bool:
    if req_id is None or req_id < 0:
        return False
    now = monotonic()
    _prune_suppressed_gateway_req_ids(now)
    expiry = _SUPPRESSED_GATEWAY_REQ_IDS.get(req_id)
    if expiry is None:
        return False
    return now <= expiry


def _prune_suppressed_gateway_req_ids(now: Optional[float] = None) -> None:
    current = monotonic() if now is None else now
    expired = [
        req_id
        for req_id, expiry in _SUPPRESSED_GATEWAY_REQ_IDS.items()
        if expiry < current
    ]
    for req_id in expired:
        _SUPPRESSED_GATEWAY_REQ_IDS.pop(req_id, None)

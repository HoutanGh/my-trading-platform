from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

from apps.core.market_data.models import Bar, Quote
from apps.core.market_data.ports import BarStreamPort, QuotePort, QuoteStreamPort
from apps.core.orders.models import (
    BracketOrderSpec,
    LadderExecutionMode,
    LadderOrderSpec,
    OrderSide,
    OrderSpec,
    OrderType,
)
from apps.core.orders.ports import EventBus
from apps.core.orders.service import OrderService
from apps.core.strategies.breakout.events import (
    BreakoutBreakDetected,
    BreakoutConfirmed,
    BreakoutFastTriggered,
    BreakoutRejected,
    BreakoutStarted,
    BreakoutStopped,
)
from apps.core.strategies.breakout.logic import (
    BreakoutAction,
    FastEntryThresholds,
    BreakoutRuleConfig,
    BreakoutState,
    evaluate_breakout,
    evaluate_fast_entry,
)


@dataclass(frozen=True)
class BreakoutRunConfig:
    symbol: str
    qty: int
    rule: BreakoutRuleConfig
    entry_type: OrderType = OrderType.LIMIT
    take_profit: Optional[float] = None
    take_profits: Optional[list[float]] = None
    take_profit_qtys: Optional[list[int]] = None
    stop_loss: Optional[float] = None
    use_rth: bool = False
    bar_size: str = "1 min"
    fast_bar_size: str = "1 secs"
    max_bars: Optional[int] = None
    tif: str = "DAY"
    outside_rth: bool = False
    account: Optional[str] = None
    client_tag: Optional[str] = None
    quote_max_age_seconds: float = 2.0
    quote_warmup_seconds: float = 2.0
    quote_snapshot_fallback: bool = True
    tp_reprice_on_fill: bool = False
    tp_reprice_bar_size: str = "1 min"
    tp_reprice_use_rth: bool = False
    tp_reprice_timeout_seconds: float = 5.0
    ladder_execution_mode: LadderExecutionMode = LadderExecutionMode.ATTACHED


async def run_breakout(
    config: BreakoutRunConfig,
    *,
    bar_stream: BarStreamPort,
    order_service: OrderService,
    quote_port: QuotePort | None = None,
    quote_stream: QuoteStreamPort | None = None,
    event_bus: EventBus | None = None,
) -> None:
    symbol = config.symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if config.qty <= 0:
        raise ValueError("qty must be greater than zero")
    if config.rule.level <= 0:
        raise ValueError("breakout level must be greater than zero")
    if config.take_profit is not None and config.take_profits:
        raise ValueError("take_profit and take_profits cannot both be provided")
    if (config.take_profit is None and not config.take_profits) ^ (config.stop_loss is None):
        raise ValueError("take_profit and stop_loss must be provided together")
    if config.take_profit_qtys is not None and not config.take_profits:
        raise ValueError("take_profit_qtys requires take_profits")
    if config.take_profits and config.take_profit_qtys is not None:
        if len(config.take_profits) != len(config.take_profit_qtys):
            raise ValueError("take_profit_qtys must match take_profits")
        if any(qty <= 0 for qty in config.take_profit_qtys):
            raise ValueError("take_profit_qtys must be greater than zero")
        if sum(config.take_profit_qtys) != config.qty:
            raise ValueError("take_profit_qtys must sum to qty")
    if config.tp_reprice_timeout_seconds <= 0:
        raise ValueError("tp_reprice_timeout_seconds must be greater than zero")
    if config.ladder_execution_mode == LadderExecutionMode.ATTACHED and config.take_profits:
        raise ValueError("ATTACHED ladder mode is not supported; use bracket for 1 TP + 1 SL")
    if config.ladder_execution_mode == LadderExecutionMode.DETACHED:
        if not config.take_profits or len(config.take_profits) != 3:
            raise ValueError("DETACHED requires exactly 3 take_profits")
    if config.ladder_execution_mode == LadderExecutionMode.DETACHED_70_30:
        if not config.take_profits or len(config.take_profits) != 2:
            raise ValueError("DETACHED_70_30 requires exactly 2 take_profits")
    if config.entry_type == OrderType.LIMIT and quote_port is None and quote_stream is None:
        raise ValueError("quote_port is required for limit breakout entries")

    if event_bus:
        event_bus.publish(
            BreakoutStarted.now(
                symbol,
                config.rule,
                take_profit=config.take_profit,
                take_profits=config.take_profits,
                stop_loss=config.stop_loss,
            )
        )

    client_tag = config.client_tag or _default_breakout_tag(symbol, config.rule.level)
    state = BreakoutState()
    bars_seen = 0
    decision = asyncio.Event()
    decision_lock = asyncio.Lock()
    submit_owner_task: asyncio.Task | None = None
    fast_config = config.rule.fast_entry

    async def _submit_entry(
        bar: "Bar",
        *,
        reason: str,
        fast_thresholds: Optional[FastEntryThresholds] = None,
    ) -> bool:
        nonlocal submit_owner_task
        if decision.is_set():
            return False
        async with decision_lock:
            if decision.is_set():
                return False
            decision.set()
            submit_owner_task = asyncio.current_task()

        if event_bus and fast_thresholds is not None:
            event_bus.publish(
                BreakoutFastTriggered.now(
                    symbol,
                    bar,
                    config.rule.level,
                    fast_thresholds,
                    take_profit=config.take_profit,
                    take_profits=config.take_profits,
                    stop_loss=config.stop_loss,
                )
            )
        if event_bus:
            event_bus.publish(
                BreakoutConfirmed.now(
                    symbol,
                    bar,
                    config.rule.level,
                    take_profit=config.take_profit,
                    take_profits=config.take_profits,
                    stop_loss=config.stop_loss,
                    account=config.account,
                    client_tag=client_tag,
                )
            )
        entry_price = None
        if config.entry_type == OrderType.LIMIT:
            quote = None
            quote_age = None
            if quote_stream:
                quote, quote_age = await _wait_for_fresh_stream_quote(
                    quote_stream,
                    symbol,
                    max_age_seconds=config.quote_max_age_seconds,
                    warmup_seconds=config.quote_warmup_seconds,
                )
            if (quote is None or quote.ask is None) and config.quote_snapshot_fallback and quote_port:
                quote = await quote_port.get_quote(symbol)
                quote_age = _quote_age_seconds(quote)
            if not quote or quote.ask is None:
                if event_bus:
                    event_bus.publish(
                        BreakoutRejected.now(
                            symbol,
                            bar,
                            config.rule.level,
                            reason="quote_missing",
                            take_profit=config.take_profit,
                            take_profits=config.take_profits,
                            stop_loss=config.stop_loss,
                            quote_max_age_seconds=config.quote_max_age_seconds,
                        )
                    )
                    event_bus.publish(
                        BreakoutStopped.now(
                            symbol,
                            reason="quote_missing",
                            client_tag=client_tag,
                        )
                    )
                return True
            if quote_age is None:
                if event_bus:
                    event_bus.publish(
                        BreakoutRejected.now(
                            symbol,
                            bar,
                            config.rule.level,
                            reason="quote_missing",
                            take_profit=config.take_profit,
                            take_profits=config.take_profits,
                            stop_loss=config.stop_loss,
                            quote_max_age_seconds=config.quote_max_age_seconds,
                        )
                    )
                    event_bus.publish(
                        BreakoutStopped.now(
                            symbol,
                            reason="quote_missing",
                            client_tag=client_tag,
                        )
                    )
                return True
            if _is_quote_stale(quote_age, config.quote_max_age_seconds):
                if event_bus:
                    event_bus.publish(
                        BreakoutRejected.now(
                            symbol,
                            bar,
                            config.rule.level,
                            reason="quote_stale",
                            take_profit=config.take_profit,
                            take_profits=config.take_profits,
                            stop_loss=config.stop_loss,
                            quote_age_seconds=quote_age,
                            quote_max_age_seconds=config.quote_max_age_seconds,
                        )
                    )
                    event_bus.publish(
                        BreakoutStopped.now(
                            symbol,
                            reason="quote_stale",
                            client_tag=client_tag,
                        )
                    )
                return True
            entry_price = quote.ask
        if config.take_profits:
            tp_levels = config.take_profits
            tp_qtys = config.take_profit_qtys or _split_take_profit_qtys(config.qty, len(tp_levels))
            stop_updates = _stop_updates_for_take_profits(tp_levels, config.rule.level)
            spec = LadderOrderSpec(
                symbol=symbol,
                qty=config.qty,
                side=OrderSide.BUY,
                entry_type=config.entry_type,
                entry_price=entry_price,
                take_profits=tp_levels,
                take_profit_qtys=tp_qtys,
                stop_loss=config.stop_loss or 0.0,
                stop_limit_offset=0.0,
                stop_updates=stop_updates,
                tif=config.tif,
                outside_rth=config.outside_rth,
                account=config.account,
                client_tag=client_tag,
                execution_mode=config.ladder_execution_mode,
            )
            await order_service.submit_ladder(spec)
        elif config.take_profit is not None and config.stop_loss is not None:
            spec = BracketOrderSpec(
                symbol=symbol,
                qty=config.qty,
                side=OrderSide.BUY,
                entry_type=config.entry_type,
                entry_price=entry_price,
                take_profit=config.take_profit,
                stop_loss=config.stop_loss,
                tif=config.tif,
                outside_rth=config.outside_rth,
                account=config.account,
                client_tag=client_tag,
            )
            await order_service.submit_bracket(spec)
        else:
            spec = OrderSpec(
                symbol=symbol,
                qty=config.qty,
                side=OrderSide.BUY,
                order_type=config.entry_type,
                limit_price=entry_price,
                tif=config.tif,
                outside_rth=config.outside_rth,
                account=config.account,
                client_tag=client_tag,
            )
            await order_service.submit_order(spec)
        if event_bus:
            event_bus.publish(
                BreakoutStopped.now(
                    symbol,
                    reason=reason,
                    client_tag=client_tag,
                )
            )
        return True
    async def _watch_slow() -> None:
        nonlocal bars_seen, state
        async for bar in bar_stream.stream_bars(symbol, bar_size=config.bar_size, use_rth=config.use_rth):
            if decision.is_set():
                return
            bars_seen += 1
            was_break_seen = state.break_seen
            state, action = evaluate_breakout(state, bar, config.rule)

            if not was_break_seen and state.break_seen and event_bus:
                event_bus.publish(
                    BreakoutBreakDetected.now(
                        symbol,
                        bar,
                        config.rule.level,
                        take_profit=config.take_profit,
                        take_profits=config.take_profits,
                        stop_loss=config.stop_loss,
                    )
                )

            if action == BreakoutAction.ENTER:
                await _submit_entry(bar, reason="order_submitted")
                return

            if action == BreakoutAction.STOP:
                if event_bus:
                    event_bus.publish(
                        BreakoutRejected.now(
                            symbol,
                            bar,
                            config.rule.level,
                            reason="confirm_failed",
                            take_profit=config.take_profit,
                            take_profits=config.take_profits,
                            stop_loss=config.stop_loss,
                        )
                    )
                    event_bus.publish(
                        BreakoutStopped.now(
                            symbol,
                            reason="confirm_failed",
                            client_tag=client_tag,
                        )
                    )
                return

            if config.max_bars is not None and bars_seen >= config.max_bars:
                if event_bus:
                    event_bus.publish(
                        BreakoutStopped.now(
                            symbol,
                            reason="max_bars",
                            client_tag=client_tag,
                        )
                    )
                return

    async def _watch_fast() -> None:
        if not fast_config.enabled:
            return
        async for bar in bar_stream.stream_bars(symbol, bar_size=config.fast_bar_size, use_rth=config.use_rth):
            if decision.is_set():
                return
            thresholds = evaluate_fast_entry(bar, level=config.rule.level, config=fast_config)
            if thresholds is None:
                continue
            await _submit_entry(
                bar,
                reason="order_submitted_fast",
                fast_thresholds=thresholds,
            )
            return

    tasks: list[asyncio.Task] = []
    stream_active = False
    if quote_stream and config.entry_type == OrderType.LIMIT:
        try:
            stream_active = await quote_stream.subscribe(symbol)
        except Exception:
            stream_active = False

    try:
        tasks = [asyncio.create_task(_watch_slow(), name=f"breakout:slow:{symbol}")]
        if fast_config.enabled:
            tasks.append(asyncio.create_task(_watch_fast(), name=f"breakout:fast:{symbol}"))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            if task is submit_owner_task:
                continue
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in tasks:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc:
                raise exc
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if event_bus:
            event_bus.publish(
                BreakoutStopped.now(
                    symbol,
                    reason="cancelled",
                    client_tag=client_tag,
                )
            )
        raise
    finally:
        if quote_stream and stream_active:
            await quote_stream.unsubscribe(symbol)


def _split_take_profit_qtys(total_qty: int, count: int) -> list[int]:
    if count == 2:
        ratios = [0.7, 0.3]
    elif count == 3:
        ratios = [0.6, 0.3, 0.1]
    else:
        raise ValueError("only 2 or 3 take profits are supported")

    raw = [total_qty * ratio for ratio in ratios]
    qtys = [int(math.floor(value)) for value in raw]
    remainder = total_qty - sum(qtys)
    if remainder > 0:
        fractions = [(idx, raw[idx] - qtys[idx]) for idx in range(len(qtys))]
        fractions.sort(key=lambda item: (-item[1], item[0]))
        for idx, _fraction in fractions[:remainder]:
            qtys[idx] += 1
    if any(qty <= 0 for qty in qtys):
        raise ValueError("qty too small for take profit ladder")
    return qtys


def _stop_updates_for_take_profits(take_profits: list[float], breakout_level: float) -> list[float]:
    if len(take_profits) == 2:
        return [breakout_level]
    if len(take_profits) == 3:
        return [breakout_level, take_profits[0]]
    raise ValueError("only 2 or 3 take profits are supported")


def _default_breakout_tag(symbol: str, level: float) -> str:
    level_str = f"{level:g}"
    return f"breakout:{symbol}:{level_str}"


def _is_quote_stale(quote_age_seconds: float, max_age_seconds: float) -> bool:
    if max_age_seconds <= 0:
        return False
    return quote_age_seconds > max_age_seconds


def _quote_age_seconds(quote: Quote) -> Optional[float]:
    timestamp = getattr(quote, "timestamp", None)
    if timestamp is None:
        return None
    timestamp = _normalize_timestamp(timestamp)
    return (datetime.now(timezone.utc) - timestamp).total_seconds()


async def _wait_for_fresh_stream_quote(
    quote_stream: QuoteStreamPort,
    symbol: str,
    *,
    max_age_seconds: float,
    warmup_seconds: float,
    poll_interval: float = 0.1,
) -> tuple[Optional[Quote], Optional[float]]:
    deadline = time.time() + max(warmup_seconds, 0.0)
    quote = quote_stream.get_latest(symbol)
    quote_age = _quote_age_seconds(quote) if quote else None
    if quote and quote.ask is not None and quote_age is not None:
        if not _is_quote_stale(quote_age, max_age_seconds):
            return quote, quote_age
    if warmup_seconds <= 0:
        return quote, quote_age
    while time.time() < deadline:
        await asyncio.sleep(poll_interval)
        quote = quote_stream.get_latest(symbol)
        quote_age = _quote_age_seconds(quote) if quote else None
        if quote and quote.ask is not None and quote_age is not None:
            if not _is_quote_stale(quote_age, max_age_seconds):
                return quote, quote_age
    return quote, quote_age


def _normalize_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp

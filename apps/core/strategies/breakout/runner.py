from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

from apps.core.market_data.models import Quote
from apps.core.market_data.ports import BarStreamPort, QuotePort
from apps.core.orders.models import BracketOrderSpec, OrderSide, OrderSpec, OrderType
from apps.core.orders.ports import EventBus
from apps.core.orders.service import OrderService
from apps.core.strategies.breakout.events import (
    BreakoutBreakDetected,
    BreakoutConfirmed,
    BreakoutRejected,
    BreakoutStarted,
    BreakoutStopped,
)
from apps.core.strategies.breakout.logic import (
    BreakoutAction,
    BreakoutRuleConfig,
    BreakoutState,
    evaluate_breakout,
)


@dataclass(frozen=True)
class BreakoutRunConfig:
    symbol: str
    qty: int
    rule: BreakoutRuleConfig
    entry_type: OrderType = OrderType.LIMIT
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    use_rth: bool = False
    bar_size: str = "1 min"
    max_bars: Optional[int] = None
    tif: str = "DAY"
    outside_rth: bool = False
    account: Optional[str] = None
    client_tag: Optional[str] = None
    quote_max_age_seconds: float = 2.0


async def run_breakout(
    config: BreakoutRunConfig,
    *,
    bar_stream: BarStreamPort,
    order_service: OrderService,
    quote_port: QuotePort | None = None,
    event_bus: EventBus | None = None,
) -> None:
    symbol = config.symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if config.qty <= 0:
        raise ValueError("qty must be greater than zero")
    if config.rule.level <= 0:
        raise ValueError("breakout level must be greater than zero")
    if (config.take_profit is None) ^ (config.stop_loss is None):
        raise ValueError("take_profit and stop_loss must be provided together")
    if config.entry_type == OrderType.LIMIT and quote_port is None:
        raise ValueError("quote_port is required for limit breakout entries")

    if event_bus:
        event_bus.publish(BreakoutStarted.now(symbol, config.rule))

    client_tag = config.client_tag or _default_breakout_tag(symbol, config.rule.level)
    state = BreakoutState()
    bars_seen = 0

    try:
        async for bar in bar_stream.stream_bars(symbol, bar_size=config.bar_size, use_rth=config.use_rth):
            bars_seen += 1
            was_break_seen = state.break_seen
            state, action = evaluate_breakout(state, bar, config.rule)

            if not was_break_seen and state.break_seen and event_bus:
                event_bus.publish(BreakoutBreakDetected.now(symbol, bar, config.rule.level))

            if action == BreakoutAction.ENTER:
                if event_bus:
                    event_bus.publish(
                        BreakoutConfirmed.now(
                            symbol,
                            bar,
                            config.rule.level,
                            take_profit=config.take_profit,
                            stop_loss=config.stop_loss,
                            account=config.account,
                            client_tag=client_tag,
                        )
                    )
                entry_price = None
                if config.entry_type == OrderType.LIMIT:
                    quote = await quote_port.get_quote(symbol) if quote_port else None
                    if not quote or quote.ask is None:
                        if event_bus:
                            event_bus.publish(
                                BreakoutRejected.now(
                                    symbol,
                                    bar,
                                    config.rule.level,
                                    reason="quote_missing",
                                )
                            )
                            event_bus.publish(
                                BreakoutStopped.now(
                                    symbol,
                                    reason="quote_missing",
                                    client_tag=client_tag,
                                )
                            )
                        return
                    if _is_quote_stale(quote, config.quote_max_age_seconds):
                        if event_bus:
                            event_bus.publish(
                                BreakoutRejected.now(
                                    symbol,
                                    bar,
                                    config.rule.level,
                                    reason="quote_stale",
                                )
                            )
                            event_bus.publish(
                                BreakoutStopped.now(
                                    symbol,
                                    reason="quote_stale",
                                    client_tag=client_tag,
                                )
                            )
                        return
                    entry_price = quote.ask
                if config.take_profit is not None and config.stop_loss is not None:
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
                            reason="order_submitted",
                            client_tag=client_tag,
                        )
                    )
                return

            if action == BreakoutAction.STOP:
                if event_bus:
                    event_bus.publish(
                        BreakoutRejected.now(
                            symbol,
                            bar,
                            config.rule.level,
                            reason="confirm_failed",
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
    except asyncio.CancelledError:
        if event_bus:
            event_bus.publish(
                BreakoutStopped.now(
                    symbol,
                    reason="cancelled",
                    client_tag=client_tag,
                )
            )
        raise


def _default_breakout_tag(symbol: str, level: float) -> str:
    level_str = f"{level:g}"
    return f"breakout:{symbol}:{level_str}"


def _is_quote_stale(quote: Quote, max_age_seconds: float) -> bool:
    if max_age_seconds <= 0:
        return False
    timestamp = _normalize_timestamp(quote.timestamp)
    age_seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()
    return age_seconds > max_age_seconds


def _normalize_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp

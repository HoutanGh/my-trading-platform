from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from apps.core.analytics.flow.take_profit import TakeProfitConfig, TakeProfitResult, compute_take_profits
from apps.core.market_data.ports import BarHistoryPort


@dataclass(frozen=True)
class TakeProfitRequest:
    symbol: str
    bar_size: str = "1 min"
    use_rth: bool = False
    end: datetime | None = None
    config: TakeProfitConfig = field(default_factory=TakeProfitConfig)


class TakeProfitService:
    def __init__(self, history_port: BarHistoryPort) -> None:
        self._history_port = history_port

    async def compute_levels(self, request: TakeProfitRequest) -> TakeProfitResult:
        symbol = request.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        config = request.config
        if not config.lookback_days:
            raise ValueError("lookback_days must not be empty")

        end_ts = request.end or datetime.now(timezone.utc)
        last_result: TakeProfitResult | None = None

        for lookback_days in config.lookback_days:
            start_ts = _calendar_lookback_start(end_ts, lookback_days)
            bars = await self._history_port.fetch_bars(
                symbol,
                bar_size=request.bar_size,
                start=start_ts,
                end=end_ts,
                use_rth=request.use_rth,
            )
            if not bars:
                continue

            result = compute_take_profits(
                bars,
                current_price=bars[-1].close,
                config=config,
            )
            last_result = result
            if len(result.levels) >= config.min_levels and not result.used_fallback:
                return _with_lookback(result, lookback_days)

        if last_result is None:
            return TakeProfitResult(levels=[], used_fallback=False, lookback_days=None)
        return _with_lookback(last_result, config.lookback_days[-1])


def _calendar_lookback_start(end_ts: datetime, lookback_days: int) -> datetime:
    # TODO: Replace with a trading-calendar-aware lookback.
    return end_ts - timedelta(days=lookback_days)


def _with_lookback(result: TakeProfitResult, lookback_days: int) -> TakeProfitResult:
    if result.lookback_days == lookback_days:
        return result
    return TakeProfitResult(
        levels=list(result.levels),
        used_fallback=result.used_fallback,
        lookback_days=lookback_days,
    )

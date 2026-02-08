from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from apps.core.analytics.flow.take_profit.calculator import (
    TakeProfitConfig,
    TakeProfitReason,
    TakeProfitResult,
    compute_take_profits,
)
from apps.core.market_data.ports import BarHistoryPort


@dataclass(frozen=True)
class TakeProfitRequest:
    symbol: str
    bar_size: str = "1 min"
    use_rth: bool = False
    end: datetime | None = None
    anchor_price: float | None = None
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
        last_result: tuple[TakeProfitResult, int] | None = None

        for lookback_days in config.lookback_days:
            start_ts = _calendar_lookback_start(end_ts, lookback_days)
            candidate_bar_sizes = _bar_size_candidates(request.bar_size, lookback_days)
            lookback_result: TakeProfitResult | None = None
            for candidate_bar_size in candidate_bar_sizes:
                try:
                    bars = await self._history_port.fetch_bars(
                        symbol,
                        bar_size=candidate_bar_size,
                        start=start_ts,
                        end=end_ts,
                        use_rth=request.use_rth,
                    )
                except RuntimeError:
                    # Keep the best prior result if only wider windows fail.
                    if last_result is not None:
                        return _with_lookback(last_result[0], last_result[1])
                    raise
                if not bars:
                    # HMDS requests can return empty bars on timeout; retry with
                    # coarser bar sizes for deeper lookbacks.
                    continue

                result = compute_take_profits(
                    bars,
                    current_price=request.anchor_price if request.anchor_price is not None else bars[-1].close,
                    config=config,
                )
                lookback_result = result
                if len(result.levels) >= config.min_levels and _is_structured(result, config):
                    return _with_lookback(result, lookback_days)

            if lookback_result is not None:
                last_result = (lookback_result, lookback_days)

        if last_result is None:
            return TakeProfitResult(levels=[], used_fallback=False, lookback_days=None)
        return _with_lookback(last_result[0], last_result[1])


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


def _is_structured(result: TakeProfitResult, config: TakeProfitConfig) -> bool:
    required = max(0, min(config.min_levels, config.volume_min_levels_to_accept))
    volume_count = sum(1 for level in result.levels if level.reason == TakeProfitReason.VOLUME)
    return volume_count >= required


def _bar_size_candidates(default_bar_size: str, lookback_days: int) -> list[str]:
    normalized = default_bar_size.strip().lower()
    if normalized not in {"1 min", "1 mins", "1 minute", "1 minutes"}:
        return [default_bar_size]
    if lookback_days <= 20:
        return ["1 min"]
    if lookback_days <= 60:
        return ["5 mins", "15 mins"]
    return ["15 mins", "30 mins"]

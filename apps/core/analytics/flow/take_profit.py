from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from apps.core.market_data.models import Bar


class TakeProfitReason(str, Enum):
    SWING = "swing"
    PRIOR = "prior"
    VOLUME = "volume"
    VOLATILITY = "volatility"


@dataclass(frozen=True)
class TakeProfitLevel:
    price: float
    reason: TakeProfitReason


@dataclass(frozen=True)
class TakeProfitResult:
    levels: list[TakeProfitLevel]
    used_fallback: bool
    lookback_days: int | None


@dataclass(frozen=True)
class TakeProfitConfig:
    lookback_days: list[int] = field(default_factory=lambda: [5, 20, 60])
    min_levels: int = 3
    # Distance thresholds are expressed as either ATR multiples or % of price.
    # The implementation should treat the effective threshold as the max of the
    # configured ATR-based and % based values (when both are provided).
    too_close_atr_mult: float = 0.25
    too_close_pct: float = 0.003
    cluster_atr_mult: float = 0.5
    cluster_pct: float = 0.005
    atr_window: int = 14
    volume_zone_top_n: int = 20


def compute_take_profits(
    bars: Sequence[Bar],
    *,
    current_price: float | None = None,
    config: TakeProfitConfig | None = None,
) -> TakeProfitResult:
    """
    Compute take-profit levels for a long position from OHLCV bars.

    Contract:
    - Uses only historical bars provided in `bars`.
    - Returns up to three TP levels (TP1/TP2/TP3) with short reasons.
    - Does not perform adaptive lookback; callers should handle expanding windows.
    - Falls back to volatility-based targets if no meaningful levels are found.
    """
    if not bars:
        return TakeProfitResult(levels=[], used_fallback=False, lookback_days=None)

    cfg = config or TakeProfitConfig()
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    price = current_price if current_price is not None else ordered[-1].close
    if price <= 0:
        return TakeProfitResult(levels=[], used_fallback=False, lookback_days=None)

    atr_value = _atr(ordered, cfg.atr_window)

    candidates: list[tuple[float, TakeProfitReason]] = []
    candidates.extend((level, TakeProfitReason.SWING) for level in _swing_highs(ordered, left=2, right=2))

    prior_day_high, prior_week_high = _prior_highs(ordered)
    if prior_day_high is not None:
        candidates.append((prior_day_high, TakeProfitReason.PRIOR))
    if prior_week_high is not None:
        candidates.append((prior_week_high, TakeProfitReason.PRIOR))

    candidates.extend(
        (level, TakeProfitReason.VOLUME)
        for level in _volume_zone_levels(ordered, top_n=cfg.volume_zone_top_n)
    )

    candidates = [(level, reason) for level, reason in candidates if level > price]
    if candidates:
        cluster_threshold = _threshold_value(
            atr_value,
            cfg.cluster_atr_mult,
            cfg.cluster_pct,
            price,
        )
        merged = _merge_levels(candidates, threshold=cluster_threshold)

        too_close = _threshold_value(
            atr_value,
            cfg.too_close_atr_mult,
            cfg.too_close_pct,
            price,
        )
        filtered = [
            level for level in merged if level.price >= price + too_close
        ]
        filtered.sort(key=lambda level: level.price)
    else:
        filtered = []

    if len(filtered) >= cfg.min_levels:
        return TakeProfitResult(
            levels=filtered[: cfg.min_levels],
            used_fallback=False,
            lookback_days=None,
        )

    fallback_levels = _volatility_targets(price, ordered, cfg)
    return TakeProfitResult(
        levels=fallback_levels,
        used_fallback=True,
        lookback_days=None,
    )


def _swing_highs(bars: Sequence[Bar], *, left: int = 2, right: int = 2) -> list[float]:
    if len(bars) < left + right + 1:
        return []
    highs = [bar.high for bar in bars]
    results: list[float] = []
    for idx in range(left, len(bars) - right):
        pivot = highs[idx]
        if all(pivot > highs[j] for j in range(idx - left, idx)) and all(
            pivot > highs[j] for j in range(idx + 1, idx + right + 1)
        ):
            results.append(pivot)
    return results


def _prior_highs(bars: Sequence[Bar]) -> tuple[float | None, float | None]:
    days: list[tuple[object, list[Bar]]] = []
    current_day = None
    for bar in bars:
        day = bar.timestamp.date()
        if current_day != day:
            days.append((day, [bar]))
            current_day = day
        else:
            days[-1][1].append(bar)

    if len(days) < 2:
        return None, None

    prior_day_bars = days[-2][1]
    prior_day_high = max(bar.high for bar in prior_day_bars)

    lookback_days = [bars_for_day for _day, bars_for_day in days[:-1]][-5:]
    if not lookback_days:
        return prior_day_high, None
    prior_week_high = max(bar.high for bars_for_day in lookback_days for bar in bars_for_day)
    return prior_day_high, prior_week_high


def _volume_zone_levels(bars: Sequence[Bar], *, top_n: int | None) -> list[float]:
    if not top_n or top_n <= 0:
        return []
    bars_with_volume = [bar for bar in bars if bar.volume is not None]
    if not bars_with_volume:
        return []
    sorted_by_volume = sorted(bars_with_volume, key=lambda bar: bar.volume or 0.0, reverse=True)
    return [bar.close for bar in sorted_by_volume[:top_n]]


def _merge_levels(
    levels: Iterable[tuple[float, TakeProfitReason]],
    *,
    threshold: float,
) -> list[TakeProfitLevel]:
    ordered = sorted(levels, key=lambda item: item[0])
    if not ordered:
        return []

    clusters: list[list[tuple[float, TakeProfitReason]]] = [[ordered[0]]]
    for price, reason in ordered[1:]:
        last_price = clusters[-1][-1][0]
        if abs(price - last_price) <= threshold:
            clusters[-1].append((price, reason))
        else:
            clusters.append([(price, reason)])

    results: list[TakeProfitLevel] = []
    for cluster in clusters:
        cluster_price = sum(level for level, _ in cluster) / len(cluster)
        cluster_reason = _pick_reason(reason for _level, reason in cluster)
        results.append(TakeProfitLevel(price=cluster_price, reason=cluster_reason))
    return results


def _pick_reason(reasons: Iterable[TakeProfitReason]) -> TakeProfitReason:
    priority = {
        TakeProfitReason.SWING: 1,
        TakeProfitReason.PRIOR: 2,
        TakeProfitReason.VOLUME: 3,
        TakeProfitReason.VOLATILITY: 4,
    }
    selected = None
    selected_priority = 999
    for reason in reasons:
        rank = priority.get(reason, 999)
        if rank < selected_priority:
            selected = reason
            selected_priority = rank
    return selected or TakeProfitReason.VOLUME


def _threshold_value(
    atr_value: float,
    atr_mult: float | None,
    pct: float | None,
    price: float,
) -> float:
    atr_component = atr_value * atr_mult if atr_mult is not None else 0.0
    pct_component = price * pct if pct is not None else 0.0
    return max(atr_component, pct_component)


def _atr(bars: Sequence[Bar], window: int) -> float:
    if not bars:
        return 0.0
    tr_values: list[float] = []
    prev_close = bars[0].close
    for bar in bars:
        high_low = bar.high - bar.low
        high_close = abs(bar.high - prev_close)
        low_close = abs(bar.low - prev_close)
        tr = max(high_low, high_close, low_close)
        tr_values.append(tr)
        prev_close = bar.close
    if not tr_values:
        return 0.0
    window_size = max(1, min(window, len(tr_values)))
    window_values = tr_values[-window_size:]
    return sum(window_values) / len(window_values)


def _volatility_targets(
    price: float,
    bars: Sequence[Bar],
    config: TakeProfitConfig,
) -> list[TakeProfitLevel]:
    atr_value = _atr(bars, config.atr_window)
    if atr_value <= 0:
        ranges = [bar.high - bar.low for bar in bars if bar.high >= bar.low]
        atr_value = sum(ranges) / len(ranges) if ranges else 0.0
    if atr_value <= 0:
        atr_value = price * 0.005

    levels = [
        TakeProfitLevel(price=price + atr_value * multiple, reason=TakeProfitReason.VOLATILITY)
        for multiple in (1.0, 2.0, 3.0)
    ]
    return levels

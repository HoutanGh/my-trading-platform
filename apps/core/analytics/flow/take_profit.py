from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from apps.core.market_data.models import Bar


class TakeProfitReason(str, Enum):
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
    lookback_days: list[int] = field(default_factory=lambda: [20, 60, 120, 240])
    min_levels: int = 3
    atr_window: int = 14
    anchor_atr_mults: tuple[float, float, float] = (1.5, 3.5, 8.0)
    anchor_pcts: tuple[float, float, float] = (0.15, 0.30, 0.80)
    gap_atr_mults: tuple[float, float] = (0.75, 1.5)
    gap_pcts: tuple[float, float] = (0.04, 0.08)
    volume_bin_atr_mult: float = 0.25
    volume_bin_pct: float = 0.005
    volume_bin_min: float = 0.01
    volume_peak_top_n: int = 30
    volume_recency_floor: float = 0.6
    volume_max_bins_per_bar: int = 32
    volume_min_levels_to_accept: int = 3


@dataclass(frozen=True)
class _VolumeZone:
    price: float
    score: float


def compute_take_profits(
    bars: Sequence[Bar],
    *,
    current_price: float | None = None,
    config: TakeProfitConfig | None = None,
) -> TakeProfitResult:
    """
    Compute runner-style long take-profit levels from OHLCV bars.

    The method creates anchor prices from ATR/percent floors and snaps each anchor
    to the nearest high-volume price zone above the minimum allowed price.
    """
    if not bars:
        return TakeProfitResult(levels=[], used_fallback=False, lookback_days=None)

    cfg = config or TakeProfitConfig()
    _validate_config(cfg)

    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    baseline = current_price if current_price is not None else ordered[-1].close
    if baseline <= 0:
        return TakeProfitResult(levels=[], used_fallback=False, lookback_days=None)

    atr_value = _atr(ordered, cfg.atr_window)
    anchors = _anchor_prices(baseline, atr_value, cfg)
    bin_size = _volume_bin_size(baseline, atr_value, cfg)
    zones = _volume_zones(
        ordered,
        baseline=baseline,
        bin_size=bin_size,
        top_n=cfg.volume_peak_top_n,
        recency_floor=cfg.volume_recency_floor,
        max_bins_per_bar=cfg.volume_max_bins_per_bar,
    )
    levels = _snap_anchors_to_zones(
        anchors,
        zones,
        baseline=baseline,
        atr_value=atr_value,
        cfg=cfg,
    )
    levels = levels[: cfg.min_levels]
    used_fallback = any(level.reason == TakeProfitReason.VOLATILITY for level in levels)
    return TakeProfitResult(levels=levels, used_fallback=used_fallback, lookback_days=None)


def _validate_config(config: TakeProfitConfig) -> None:
    if len(config.anchor_atr_mults) != 3 or len(config.anchor_pcts) != 3:
        raise ValueError("anchor_atr_mults and anchor_pcts must each have 3 values")
    if len(config.gap_atr_mults) != 2 or len(config.gap_pcts) != 2:
        raise ValueError("gap_atr_mults and gap_pcts must each have 2 values")
    if config.min_levels <= 0:
        raise ValueError("min_levels must be greater than zero")
    if config.volume_peak_top_n <= 0:
        raise ValueError("volume_peak_top_n must be greater than zero")
    if config.volume_max_bins_per_bar <= 0:
        raise ValueError("volume_max_bins_per_bar must be greater than zero")


def _anchor_prices(baseline: float, atr_value: float, config: TakeProfitConfig) -> list[float]:
    prices: list[float] = []
    for atr_mult, pct in zip(config.anchor_atr_mults, config.anchor_pcts):
        distance = max(atr_value * atr_mult, baseline * pct)
        prices.append(baseline + distance)
    return prices


def _volume_bin_size(baseline: float, atr_value: float, config: TakeProfitConfig) -> float:
    atr_component = atr_value * config.volume_bin_atr_mult
    pct_component = baseline * config.volume_bin_pct
    return max(config.volume_bin_min, atr_component, pct_component)


def _volume_zones(
    bars: Sequence[Bar],
    *,
    baseline: float,
    bin_size: float,
    top_n: int,
    recency_floor: float,
    max_bins_per_bar: int,
) -> list[_VolumeZone]:
    if not bars:
        return []

    volume_by_bin: dict[int, float] = {}
    total = len(bars)
    for idx, bar in enumerate(bars):
        volume = float(bar.volume or 0.0)
        if volume <= 0:
            continue
        low = min(bar.low, bar.high)
        high = max(bar.low, bar.high)
        if high <= 0:
            continue

        recency_weight = _recency_weight(idx, total, recency_floor)
        span = max(high - low, 0.0)
        bins_for_bar = max(1, min(max_bins_per_bar, int(span / bin_size) + 1))
        share = (volume * recency_weight) / bins_for_bar
        if bins_for_bar == 1:
            price = (low + high) / 2.0
            bin_idx = int(round(price / bin_size))
            volume_by_bin[bin_idx] = volume_by_bin.get(bin_idx, 0.0) + share
            continue

        step = span / (bins_for_bar - 1)
        for pos in range(bins_for_bar):
            price = low + (step * pos)
            bin_idx = int(round(price / bin_size))
            volume_by_bin[bin_idx] = volume_by_bin.get(bin_idx, 0.0) + share

    if not volume_by_bin:
        return []

    peak_zones = _local_peak_zones(volume_by_bin, bin_size=bin_size, baseline=baseline)
    if not peak_zones:
        return []
    merged = _merge_zones(peak_zones, merge_distance=bin_size * 2.0)
    strongest = sorted(merged, key=lambda zone: zone.score, reverse=True)[:top_n]
    return sorted(strongest, key=lambda zone: zone.price)


def _local_peak_zones(
    volume_by_bin: dict[int, float],
    *,
    bin_size: float,
    baseline: float,
) -> list[_VolumeZone]:
    ordered = sorted(volume_by_bin.items(), key=lambda item: item[0])
    peaks: list[_VolumeZone] = []
    for idx, (bin_idx, score) in enumerate(ordered):
        price = bin_idx * bin_size
        if price <= baseline:
            continue
        left = ordered[idx - 1][1] if idx > 0 else 0.0
        right = ordered[idx + 1][1] if idx < len(ordered) - 1 else 0.0
        if score < left or score < right:
            continue
        peaks.append(_VolumeZone(price=price, score=score))
    return peaks


def _merge_zones(zones: Sequence[_VolumeZone], *, merge_distance: float) -> list[_VolumeZone]:
    if not zones:
        return []
    ordered = sorted(zones, key=lambda zone: zone.price)
    clusters: list[list[_VolumeZone]] = [[ordered[0]]]
    for zone in ordered[1:]:
        if zone.price - clusters[-1][-1].price <= merge_distance:
            clusters[-1].append(zone)
            continue
        clusters.append([zone])

    merged: list[_VolumeZone] = []
    for cluster in clusters:
        total_score = sum(item.score for item in cluster)
        if total_score <= 0:
            continue
        avg_price = sum(item.price * item.score for item in cluster) / total_score
        merged.append(_VolumeZone(price=avg_price, score=total_score))
    return merged


def _snap_anchors_to_zones(
    anchors: Sequence[float],
    zones: Sequence[_VolumeZone],
    *,
    baseline: float,
    atr_value: float,
    cfg: TakeProfitConfig,
) -> list[TakeProfitLevel]:
    levels: list[TakeProfitLevel] = []
    used_zone_indexes: set[int] = set()
    previous_price = baseline

    for idx, anchor in enumerate(anchors):
        min_price = anchor
        if idx > 0:
            gap = max(
                atr_value * cfg.gap_atr_mults[idx - 1],
                baseline * cfg.gap_pcts[idx - 1],
            )
            min_price = max(min_price, previous_price + gap)

        zone_idx = _nearest_zone_index(
            zones,
            anchor=anchor,
            min_price=min_price,
            used_indexes=used_zone_indexes,
        )
        if zone_idx is None:
            levels.append(TakeProfitLevel(price=min_price, reason=TakeProfitReason.VOLATILITY))
            previous_price = min_price
            continue

        zone_price = zones[zone_idx].price
        used_zone_indexes.add(zone_idx)
        levels.append(TakeProfitLevel(price=zone_price, reason=TakeProfitReason.VOLUME))
        previous_price = zone_price

    return levels


def _nearest_zone_index(
    zones: Sequence[_VolumeZone],
    *,
    anchor: float,
    min_price: float,
    used_indexes: set[int],
) -> int | None:
    candidates = [
        idx
        for idx, zone in enumerate(zones)
        if idx not in used_indexes and zone.price >= min_price
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda idx: (abs(zones[idx].price - anchor), zones[idx].price),
    )


def _recency_weight(index: int, total: int, floor: float) -> float:
    if total <= 1:
        return 1.0
    bounded_floor = min(max(floor, 0.0), 1.0)
    progress = index / (total - 1)
    return bounded_floor + (1.0 - bounded_floor) * progress


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

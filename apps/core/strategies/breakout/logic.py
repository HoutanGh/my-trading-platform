from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from apps.core.market_data.models import Bar


class BreakoutAction(str, Enum):
    ENTER = "enter"
    STOP = "stop"


@dataclass(frozen=True)
class FastEntryConfig:
    enabled: bool = True
    distance_max_cents: int = 15
    distance_min_cents: int = 5
    max_spread_start_cents: int = 4
    max_spread_end_cents: int = 1
    scale_below_1: float = 1.0
    scale_1_4: float = 1.0
    scale_4_7: float = 1.1
    scale_7_10: float = 1.25
    scale_above_10: float = 1.25


@dataclass(frozen=True)
class FastEntryThresholds:
    elapsed_seconds: int
    bucket_start_seconds: int
    distance_cents: int
    max_spread_cents: int
    price_scale: float
    spread_cents: int


@dataclass(frozen=True)
class BreakoutRuleConfig:
    level: float
    fast_entry: FastEntryConfig = field(default_factory=FastEntryConfig)


@dataclass(frozen=True)
class BreakoutState:
    break_seen: bool = False
    break_bar_time: Optional[datetime] = None


def evaluate_breakout(
    state: BreakoutState,
    bar: Bar,
    config: BreakoutRuleConfig,
) -> Tuple[BreakoutState, Optional[BreakoutAction]]:
    """
    Pure breakout rule evaluation.
    - First bar with close >= level triggers ENTER.
    - Otherwise keep waiting.
    """
    if bar.close >= config.level:
        return BreakoutState(break_seen=True, break_bar_time=bar.timestamp), BreakoutAction.ENTER
    return state, None


def evaluate_fast_entry(
    bar: Bar,
    *,
    level: float,
    config: FastEntryConfig,
) -> Optional[FastEntryThresholds]:
    if not config.enabled:
        return None
    if bar.close <= level:
        return None

    elapsed_seconds = _elapsed_seconds_in_minute(bar.timestamp)
    bucket_start = _bucket_start_seconds(elapsed_seconds)
    price_scale = _scale_for_level(level, config)

    base_distance = _bucketed_linear_value(
        config.distance_max_cents,
        config.distance_min_cents,
        bucket_start,
        clamp_end=True,
    )
    base_spread = _bucketed_linear_value(
        config.max_spread_start_cents,
        config.max_spread_end_cents,
        bucket_start,
        clamp_end=True,
    )

    distance_cents = _round_cents(base_distance * price_scale)
    max_spread_cents = _round_cents(base_spread * price_scale)
    spread_cents = _round_cents(max(0.0, bar.high - bar.low) * 100)

    if bar.high < level + (distance_cents / 100.0):
        return None
    if spread_cents > max_spread_cents:
        return None

    return FastEntryThresholds(
        elapsed_seconds=elapsed_seconds,
        bucket_start_seconds=bucket_start,
        distance_cents=distance_cents,
        max_spread_cents=max_spread_cents,
        price_scale=price_scale,
        spread_cents=spread_cents,
    )


def _elapsed_seconds_in_minute(timestamp: datetime) -> int:
    minute_start = timestamp.replace(second=0, microsecond=0)
    elapsed = int((timestamp - minute_start).total_seconds())
    if elapsed < 0:
        return 0
    if elapsed > 59:
        return 59
    return elapsed


def _bucket_start_seconds(elapsed_seconds: int) -> int:
    bucket_start = (elapsed_seconds // 10) * 10
    return min(bucket_start, 50)


def _bucketed_linear_value(start: int, end: int, bucket_start: int, *, clamp_end: bool) -> int:
    if clamp_end and bucket_start >= 50:
        return end
    progress = bucket_start / 60.0
    value = start + (end - start) * progress
    return _round_cents(value)


def _round_cents(value: float) -> int:
    return int(math.floor(value + 0.5))


def _scale_for_level(level: float, config: FastEntryConfig) -> float:
    if level < 1:
        return config.scale_below_1
    if level < 4:
        return config.scale_1_4
    if level < 7:
        return config.scale_4_7
    if level < 10:
        return config.scale_7_10
    return config.scale_above_10

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ib_insync import BarData
from ib_insync.util import parseIBDatetime

from apps.core.market_data.models import Bar


def to_bar(ib_bar: BarData) -> Bar:
    timestamp = ib_bar.date
    if not isinstance(timestamp, datetime):
        timestamp = parseIBDatetime(timestamp)
    return Bar(
        timestamp=timestamp,
        open=float(ib_bar.open),
        high=float(ib_bar.high),
        low=float(ib_bar.low),
        close=float(ib_bar.close),
        volume=float(ib_bar.volume) if ib_bar.volume is not None else None,
    )


def duration_for_bar_size(bar_size: str) -> str:
    normalized = bar_size.strip().lower()
    if "sec" in normalized:
        return "1800 S"
    return "2 D"


def duration_for_window(start: datetime, end: datetime) -> str:
    total_seconds = max(0.0, (end - start).total_seconds())
    if total_seconds <= 0:
        return "1 D"
    if total_seconds < 86400:
        return f"{max(1, int(total_seconds))} S"
    days = int((total_seconds + 86399) // 86400)
    return f"{max(1, days)} D"


def bar_interval_seconds(bar_size: str) -> Optional[float]:
    normalized = bar_size.strip().lower()
    parts = normalized.split()
    if len(parts) < 2:
        return None
    try:
        value = float(parts[0])
    except ValueError:
        return None
    unit = parts[1]
    if unit.startswith("sec"):
        return value
    if unit.startswith("min"):
        return value * 60.0
    if unit.startswith("hour"):
        return value * 3600.0
    return None


def normalize_timestamp(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

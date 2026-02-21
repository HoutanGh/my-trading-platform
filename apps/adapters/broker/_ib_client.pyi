from __future__ import annotations

from datetime import datetime
from typing import Any, TypeAlias

IB: TypeAlias = Any
BarData: TypeAlias = Any
LimitOrder: TypeAlias = Any
MarketOrder: TypeAlias = Any
Stock: TypeAlias = Any
StopLimitOrder: TypeAlias = Any
StopOrder: TypeAlias = Any
Ticker: TypeAlias = Any
Trade: TypeAlias = Any

IB_CLIENT_BACKEND: str
UNSET_DOUBLE: float

def parse_ib_datetime(value: object) -> datetime: ...

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


@dataclass(frozen=True)
class Quote:
    timestamp: datetime
    bid: Optional[float] = None
    ask: Optional[float] = None

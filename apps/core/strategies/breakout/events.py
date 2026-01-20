from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apps.core.market_data.models import Bar
from apps.core.strategies.breakout.logic import BreakoutRuleConfig


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BreakoutStarted:
    symbol: str
    rule: BreakoutRuleConfig
    timestamp: datetime

    @classmethod
    def now(cls, symbol: str, rule: BreakoutRuleConfig) -> "BreakoutStarted":
        return cls(symbol=symbol, rule=rule, timestamp=_now())


@dataclass(frozen=True)
class BreakoutBreakDetected:
    symbol: str
    bar: Bar
    level: float
    timestamp: datetime

    @classmethod
    def now(cls, symbol: str, bar: Bar, level: float) -> "BreakoutBreakDetected":
        return cls(symbol=symbol, bar=bar, level=level, timestamp=_now())


@dataclass(frozen=True)
class BreakoutConfirmed:
    symbol: str
    bar: Bar
    level: float
    timestamp: datetime
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        bar: Bar,
        level: float,
        *,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> "BreakoutConfirmed":
        return cls(
            symbol=symbol,
            bar=bar,
            level=level,
            timestamp=_now(),
            take_profit=take_profit,
            stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class BreakoutRejected:
    symbol: str
    bar: Bar
    level: float
    reason: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        symbol: str,
        bar: Bar,
        level: float,
        reason: str,
    ) -> "BreakoutRejected":
        return cls(symbol=symbol, bar=bar, level=level, reason=reason, timestamp=_now())


@dataclass(frozen=True)
class BreakoutStopped:
    symbol: str
    reason: Optional[str]
    timestamp: datetime

    @classmethod
    def now(cls, symbol: str, reason: Optional[str] = None) -> "BreakoutStopped":
        return cls(symbol=symbol, reason=reason, timestamp=_now())

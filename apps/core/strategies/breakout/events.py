from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apps.core.market_data.models import Bar
from apps.core.strategies.breakout.logic import BreakoutRuleConfig, FastEntryThresholds


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BreakoutStarted:
    symbol: str
    rule: BreakoutRuleConfig
    timestamp: datetime
    take_profit: Optional[float] = None
    take_profits: Optional[list[float]] = None
    stop_loss: Optional[float] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        rule: BreakoutRuleConfig,
        *,
        take_profit: Optional[float] = None,
        take_profits: Optional[list[float]] = None,
        stop_loss: Optional[float] = None,
    ) -> "BreakoutStarted":
        return cls(
            symbol=symbol,
            rule=rule,
            timestamp=_now(),
            take_profit=take_profit,
            take_profits=take_profits,
            stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class BreakoutBreakDetected:
    symbol: str
    bar: Bar
    level: float
    timestamp: datetime
    take_profit: Optional[float] = None
    take_profits: Optional[list[float]] = None
    stop_loss: Optional[float] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        bar: Bar,
        level: float,
        *,
        take_profit: Optional[float] = None,
        take_profits: Optional[list[float]] = None,
        stop_loss: Optional[float] = None,
    ) -> "BreakoutBreakDetected":
        return cls(
            symbol=symbol,
            bar=bar,
            level=level,
            timestamp=_now(),
            take_profit=take_profit,
            take_profits=take_profits,
            stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class BreakoutFastTriggered:
    symbol: str
    bar: Bar
    level: float
    thresholds: FastEntryThresholds
    timestamp: datetime
    take_profit: Optional[float] = None
    take_profits: Optional[list[float]] = None
    stop_loss: Optional[float] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        bar: Bar,
        level: float,
        thresholds: FastEntryThresholds,
        *,
        take_profit: Optional[float] = None,
        take_profits: Optional[list[float]] = None,
        stop_loss: Optional[float] = None,
    ) -> "BreakoutFastTriggered":
        return cls(
            symbol=symbol,
            bar=bar,
            level=level,
            thresholds=thresholds,
            timestamp=_now(),
            take_profit=take_profit,
            take_profits=take_profits,
            stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class BreakoutConfirmed:
    symbol: str
    bar: Bar
    level: float
    timestamp: datetime
    take_profit: Optional[float] = None
    take_profits: Optional[list[float]] = None
    stop_loss: Optional[float] = None
    account: Optional[str] = None
    client_tag: Optional[str] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        bar: Bar,
        level: float,
        *,
        take_profit: Optional[float] = None,
        take_profits: Optional[list[float]] = None,
        stop_loss: Optional[float] = None,
        account: Optional[str] = None,
        client_tag: Optional[str] = None,
    ) -> "BreakoutConfirmed":
        return cls(
            symbol=symbol,
            bar=bar,
            level=level,
            timestamp=_now(),
            take_profit=take_profit,
            take_profits=take_profits,
            stop_loss=stop_loss,
            account=account,
            client_tag=client_tag,
        )


@dataclass(frozen=True)
class BreakoutRejected:
    symbol: str
    bar: Bar
    level: float
    reason: str
    timestamp: datetime
    take_profit: Optional[float] = None
    take_profits: Optional[list[float]] = None
    stop_loss: Optional[float] = None
    quote_age_seconds: Optional[float] = None
    quote_max_age_seconds: Optional[float] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        bar: Bar,
        level: float,
        reason: str,
        *,
        take_profit: Optional[float] = None,
        take_profits: Optional[list[float]] = None,
        stop_loss: Optional[float] = None,
        quote_age_seconds: Optional[float] = None,
        quote_max_age_seconds: Optional[float] = None,
    ) -> "BreakoutRejected":
        return cls(
            symbol=symbol,
            bar=bar,
            level=level,
            reason=reason,
            timestamp=_now(),
            take_profit=take_profit,
            take_profits=take_profits,
            stop_loss=stop_loss,
            quote_age_seconds=quote_age_seconds,
            quote_max_age_seconds=quote_max_age_seconds,
        )


@dataclass(frozen=True)
class BreakoutStopped:
    symbol: str
    reason: Optional[str]
    timestamp: datetime
    client_tag: Optional[str] = None

    @classmethod
    def now(
        cls,
        symbol: str,
        reason: Optional[str] = None,
        *,
        client_tag: Optional[str] = None,
    ) -> "BreakoutStopped":
        return cls(symbol=symbol, reason=reason, timestamp=_now(), client_tag=client_tag)

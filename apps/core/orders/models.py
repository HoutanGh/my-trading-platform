from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class LadderExecutionMode(str, Enum):
    ATTACHED = "ATTACHED"
    DETACHED = "DETACHED"
    DETACHED_70_30 = "DETACHED_70_30"


@dataclass(frozen=True)
class OrderSpec:
    symbol: str
    qty: int
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    tif: str = "DAY"
    outside_rth: bool = False
    exchange: str = "SMART"
    currency: str = "USD"
    account: Optional[str] = None
    client_tag: Optional[str] = None


@dataclass(frozen=True)
class OrderCancelSpec:
    order_id: int


@dataclass(frozen=True)
class OrderReplaceSpec:
    order_id: int
    qty: Optional[int] = None
    limit_price: Optional[float] = None
    tif: Optional[str] = None
    outside_rth: Optional[bool] = None


@dataclass(frozen=True)
class BracketOrderSpec:
    symbol: str
    qty: int
    side: OrderSide
    entry_type: OrderType = OrderType.MARKET
    entry_price: Optional[float] = None
    take_profit: float = 0.0
    stop_loss: float = 0.0
    tif: str = "DAY"
    outside_rth: bool = False
    exchange: str = "SMART"
    currency: str = "USD"
    account: Optional[str] = None
    client_tag: Optional[str] = None


@dataclass(frozen=True)
class LadderOrderSpec:
    symbol: str
    qty: int
    side: OrderSide
    entry_type: OrderType = OrderType.MARKET
    entry_price: Optional[float] = None
    take_profits: list[float] = field(default_factory=list)
    take_profit_qtys: list[int] = field(default_factory=list)
    stop_loss: float = 0.0
    stop_limit_offset: float = 0.0
    stop_updates: list[float] = field(default_factory=list)
    tif: str = "DAY"
    outside_rth: bool = False
    exchange: str = "SMART"
    currency: str = "USD"
    account: Optional[str] = None
    client_tag: Optional[str] = None
    execution_mode: LadderExecutionMode = LadderExecutionMode.ATTACHED


@dataclass(frozen=True)
class OrderAck:
    order_id: Optional[int]
    status: Optional[str]
    submitted_at: datetime

    @classmethod
    def now(cls, *, order_id: Optional[int], status: Optional[str]) -> "OrderAck":
        return cls(order_id=order_id, status=status, submitted_at=datetime.now(timezone.utc))

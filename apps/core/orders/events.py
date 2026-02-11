from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apps.core.orders.models import OrderSide, OrderSpec


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class OrderIntent:
    spec: OrderSpec
    timestamp: datetime

    @classmethod
    def now(cls, spec: OrderSpec) -> "OrderIntent":
        return cls(spec=spec, timestamp=_now())


@dataclass(frozen=True)
class OrderSent:
    spec: OrderSpec
    timestamp: datetime

    @classmethod
    def now(cls, spec: OrderSpec) -> "OrderSent":
        return cls(spec=spec, timestamp=_now())


@dataclass(frozen=True)
class OrderIdAssigned:
    spec: OrderSpec
    order_id: Optional[int]
    timestamp: datetime

    @classmethod
    def now(cls, spec: OrderSpec, order_id: Optional[int]) -> "OrderIdAssigned":
        return cls(spec=spec, order_id=order_id, timestamp=_now())


@dataclass(frozen=True)
class OrderStatusChanged:
    spec: OrderSpec
    order_id: Optional[int]
    status: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        spec: OrderSpec,
        *,
        order_id: Optional[int],
        status: Optional[str],
    ) -> "OrderStatusChanged":
        return cls(spec=spec, order_id=order_id, status=status, timestamp=_now())


@dataclass(frozen=True)
class OrderFilled:
    spec: OrderSpec
    order_id: Optional[int]
    status: Optional[str]
    filled_qty: Optional[float]
    avg_fill_price: Optional[float]
    remaining_qty: Optional[float]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        spec: OrderSpec,
        *,
        order_id: Optional[int],
        status: Optional[str],
        filled_qty: Optional[float],
        avg_fill_price: Optional[float],
        remaining_qty: Optional[float],
    ) -> "OrderFilled":
        return cls(
            spec=spec,
            order_id=order_id,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            remaining_qty=remaining_qty,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BracketChildOrderStatusChanged:
    kind: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    order_id: Optional[int]
    parent_order_id: Optional[int]
    status: Optional[str]
    client_tag: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        kind: str,
        symbol: str,
        side: OrderSide,
        qty: int,
        price: float,
        order_id: Optional[int],
        parent_order_id: Optional[int],
        status: Optional[str],
        client_tag: Optional[str],
    ) -> "BracketChildOrderStatusChanged":
        return cls(
            kind=kind,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_id=order_id,
            parent_order_id=parent_order_id,
            status=status,
            client_tag=client_tag,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BracketChildOrderFilled:
    kind: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    order_id: Optional[int]
    parent_order_id: Optional[int]
    status: Optional[str]
    filled_qty: Optional[float]
    avg_fill_price: Optional[float]
    remaining_qty: Optional[float]
    client_tag: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        kind: str,
        symbol: str,
        side: OrderSide,
        qty: int,
        price: float,
        order_id: Optional[int],
        parent_order_id: Optional[int],
        status: Optional[str],
        filled_qty: Optional[float],
        avg_fill_price: Optional[float],
        remaining_qty: Optional[float],
        client_tag: Optional[str],
    ) -> "BracketChildOrderFilled":
        return cls(
            kind=kind,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_id=order_id,
            parent_order_id=parent_order_id,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            remaining_qty=remaining_qty,
            client_tag=client_tag,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class LadderStopLossReplaced:
    symbol: str
    parent_order_id: Optional[int]
    old_order_id: Optional[int]
    new_order_id: Optional[int]
    old_qty: int
    new_qty: int
    old_price: float
    new_price: float
    reason: str
    client_tag: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        parent_order_id: Optional[int],
        old_order_id: Optional[int],
        new_order_id: Optional[int],
        old_qty: int,
        new_qty: int,
        old_price: float,
        new_price: float,
        reason: str,
        client_tag: Optional[str],
    ) -> "LadderStopLossReplaced":
        return cls(
            symbol=symbol,
            parent_order_id=parent_order_id,
            old_order_id=old_order_id,
            new_order_id=new_order_id,
            old_qty=old_qty,
            new_qty=new_qty,
            old_price=old_price,
            new_price=new_price,
            reason=reason,
            client_tag=client_tag,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class LadderStopLossCancelled:
    symbol: str
    parent_order_id: Optional[int]
    order_id: Optional[int]
    qty: int
    price: float
    reason: str
    client_tag: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        parent_order_id: Optional[int],
        order_id: Optional[int],
        qty: int,
        price: float,
        reason: str,
        client_tag: Optional[str],
    ) -> "LadderStopLossCancelled":
        return cls(
            symbol=symbol,
            parent_order_id=parent_order_id,
            order_id=order_id,
            qty=qty,
            price=price,
            reason=reason,
            client_tag=client_tag,
            timestamp=_now(),
        )

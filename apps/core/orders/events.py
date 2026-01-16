from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apps.core.orders.models import OrderSpec


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

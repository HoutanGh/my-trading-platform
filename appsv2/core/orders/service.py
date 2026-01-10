from __future__ import annotations

from dataclasses import replace
from typing import Optional, Type, TypeVar

from appsv2.core.orders.events import OrderIntent
from appsv2.core.orders.models import OrderAck, OrderSide, OrderSpec, OrderType
from appsv2.core.orders.ports import EventBus, OrderPort

_EnumT = TypeVar("_EnumT", bound=object)


class OrderValidationError(ValueError):
    """Raised when an OrderSpec fails validation."""


class OrderService:
    def __init__(self, order_port: OrderPort, event_bus: Optional[EventBus] = None) -> None:
        self._order_port = order_port
        self._event_bus = event_bus

    async def submit_order(self, spec: OrderSpec) -> OrderAck:
        normalized = self._normalize_spec(spec)
        self._validate(normalized)
        if self._event_bus:
            self._event_bus.publish(OrderIntent.now(normalized))
        return await self._order_port.submit_order(normalized)

    def _normalize_spec(self, spec: OrderSpec) -> OrderSpec:
        side = _coerce_enum(OrderSide, spec.side, "side")
        order_type = _coerce_enum(OrderType, spec.order_type, "order_type")
        symbol = spec.symbol.strip().upper()
        tif = spec.tif.strip().upper() if spec.tif else "DAY"
        exchange = spec.exchange.strip().upper() if spec.exchange else "SMART"
        currency = spec.currency.strip().upper() if spec.currency else "USD"

        return replace(
            spec,
            symbol=symbol,
            side=side,
            order_type=order_type,
            tif=tif,
            exchange=exchange,
            currency=currency,
        )

    def _validate(self, spec: OrderSpec) -> None:
        if not spec.symbol:
            raise OrderValidationError("symbol is required")
        if spec.qty <= 0:
            raise OrderValidationError("qty must be greater than zero")
        if spec.order_type == OrderType.LIMIT:
            if spec.limit_price is None:
                raise OrderValidationError("limit_price is required for limit orders")
            if spec.limit_price <= 0:
                raise OrderValidationError("limit_price must be greater than zero")
        if spec.order_type == OrderType.MARKET and spec.limit_price is not None:
            raise OrderValidationError("limit_price is not valid for market orders")
        if not spec.tif:
            raise OrderValidationError("tif is required")


def _coerce_enum(enum_cls: Type[_EnumT], value: object, name: str) -> _EnumT:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        try:
            return enum_cls(normalized)  # type: ignore[arg-type]
        except ValueError:
            pass
    raise OrderValidationError(f"invalid {name}: {value}")

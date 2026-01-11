from __future__ import annotations

from dataclasses import replace
from typing import Optional, Type, TypeVar

from apps.core.orders.events import OrderIntent
from apps.core.orders.models import BracketOrderSpec, OrderAck, OrderSide, OrderSpec, OrderType
from apps.core.orders.ports import EventBus, OrderPort

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

    async def submit_bracket(self, spec: BracketOrderSpec) -> OrderAck:
        normalized = self._normalize_bracket_spec(spec)
        self._validate_bracket(normalized)
        if self._event_bus:
            self._event_bus.publish(OrderIntent.now(_entry_spec_from_bracket(normalized)))
        return await self._order_port.submit_bracket_order(normalized)

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

    def _normalize_bracket_spec(self, spec: BracketOrderSpec) -> BracketOrderSpec:
        side = _coerce_enum(OrderSide, spec.side, "side")
        entry_type = _coerce_enum(OrderType, spec.entry_type, "entry_type")
        symbol = spec.symbol.strip().upper()
        tif = spec.tif.strip().upper() if spec.tif else "DAY"
        exchange = spec.exchange.strip().upper() if spec.exchange else "SMART"
        currency = spec.currency.strip().upper() if spec.currency else "USD"

        return replace(
            spec,
            symbol=symbol,
            side=side,
            entry_type=entry_type,
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

    def _validate_bracket(self, spec: BracketOrderSpec) -> None:
        if not spec.symbol:
            raise OrderValidationError("symbol is required")
        if spec.qty <= 0:
            raise OrderValidationError("qty must be greater than zero")
        if spec.entry_type == OrderType.LIMIT:
            if spec.entry_price is None:
                raise OrderValidationError("entry_price is required for limit entries")
            if spec.entry_price <= 0:
                raise OrderValidationError("entry_price must be greater than zero")
        if spec.entry_type == OrderType.MARKET and spec.entry_price is not None:
            raise OrderValidationError("entry_price is not valid for market entries")
        if spec.take_profit <= 0:
            raise OrderValidationError("take_profit must be greater than zero")
        if spec.stop_loss <= 0:
            raise OrderValidationError("stop_loss must be greater than zero")
        if spec.side == OrderSide.BUY and spec.take_profit <= spec.stop_loss:
            raise OrderValidationError("take_profit must be above stop_loss for BUY")
        if spec.side == OrderSide.SELL and spec.take_profit >= spec.stop_loss:
            raise OrderValidationError("take_profit must be below stop_loss for SELL")


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


def _entry_spec_from_bracket(spec: BracketOrderSpec) -> OrderSpec:
    return OrderSpec(
        symbol=spec.symbol,
        qty=spec.qty,
        side=spec.side,
        order_type=spec.entry_type,
        limit_price=spec.entry_price,
        tif=spec.tif,
        outside_rth=spec.outside_rth,
        exchange=spec.exchange,
        currency=spec.currency,
        account=spec.account,
        client_tag=spec.client_tag,
    )

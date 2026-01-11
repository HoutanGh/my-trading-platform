from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from ib_insync import IB, LimitOrder, MarketOrder, Stock, StopOrder, Trade

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.orders.events import (
    OrderIdAssigned,
    OrderSent,
    OrderStatusChanged,
)
from apps.core.orders.models import BracketOrderSpec, OrderAck, OrderSide, OrderSpec, OrderType
from apps.core.orders.ports import EventBus, OrderPort


class IBKROrderPort(OrderPort):
    def __init__(self, connection: IBKRConnection, event_bus: EventBus | None = None) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._event_bus = event_bus

    async def submit_order(self, spec: OrderSpec) -> OrderAck:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        contract = Stock(spec.symbol, spec.exchange, spec.currency)
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {spec.symbol}")
        qualified = contracts[0]

        if spec.order_type == OrderType.MARKET:
            order = MarketOrder(spec.side.value, spec.qty, tif=spec.tif)
        elif spec.order_type == OrderType.LIMIT:
            order = LimitOrder(spec.side.value, spec.qty, spec.limit_price, tif=spec.tif)
        else:
            raise RuntimeError(f"Unsupported order type: {spec.order_type}")

        order.outsideRth = spec.outside_rth
        if spec.account:
            order.account = spec.account
        if spec.client_tag:
            order.orderRef = spec.client_tag

        trade = self._ib.placeOrder(qualified, order)
        if self._event_bus:
            self._event_bus.publish(OrderSent.now(spec))
        order_id = await _wait_for_order_id(trade)
        if self._event_bus:
            self._event_bus.publish(OrderIdAssigned.now(spec, order_id))
        status = await _wait_for_order_status(trade)
        if self._event_bus:
            self._event_bus.publish(
                OrderStatusChanged.now(spec, order_id=order_id, status=status)
            )
        return OrderAck.now(order_id=order_id, status=status)

    async def submit_bracket_order(self, spec: BracketOrderSpec) -> OrderAck:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        contract = Stock(spec.symbol, spec.exchange, spec.currency)
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {spec.symbol}")
        qualified = contracts[0]

        if spec.entry_type == OrderType.MARKET:
            parent = MarketOrder(spec.side.value, spec.qty, tif=spec.tif)
        elif spec.entry_type == OrderType.LIMIT:
            parent = LimitOrder(spec.side.value, spec.qty, spec.entry_price, tif=spec.tif)
        else:
            raise RuntimeError(f"Unsupported entry type: {spec.entry_type}")

        parent.transmit = False
        parent.outsideRth = spec.outside_rth
        if spec.account:
            parent.account = spec.account
        if spec.client_tag:
            parent.orderRef = spec.client_tag

        child_side = "SELL" if spec.side == OrderSide.BUY else "BUY"
        oca_group = f"BRKT-{uuid.uuid4().hex[:10]}"
        take_profit = LimitOrder(child_side, spec.qty, spec.take_profit, tif=spec.tif)
        take_profit.ocaGroup = oca_group
        take_profit.transmit = False
        take_profit.outsideRth = spec.outside_rth
        if spec.account:
            take_profit.account = spec.account
        if spec.client_tag:
            take_profit.orderRef = spec.client_tag

        stop_loss = StopOrder(child_side, spec.qty, spec.stop_loss, tif=spec.tif)
        stop_loss.ocaGroup = oca_group
        stop_loss.transmit = True
        stop_loss.outsideRth = spec.outside_rth
        if spec.account:
            stop_loss.account = spec.account
        if spec.client_tag:
            stop_loss.orderRef = spec.client_tag

        parent_spec = _entry_spec_from_bracket(spec)
        trade = self._ib.placeOrder(qualified, parent)
        if self._event_bus:
            self._event_bus.publish(OrderSent.now(parent_spec))
        order_id = await _wait_for_order_id(trade)
        take_profit.parentId = order_id
        stop_loss.parentId = order_id
        self._ib.placeOrder(qualified, take_profit)
        self._ib.placeOrder(qualified, stop_loss)
        if self._event_bus:
            self._event_bus.publish(OrderIdAssigned.now(parent_spec, order_id))
        status = await _wait_for_order_status(trade)
        if self._event_bus:
            self._event_bus.publish(
                OrderStatusChanged.now(parent_spec, order_id=order_id, status=status)
            )
        return OrderAck.now(order_id=order_id, status=status)


async def _wait_for_order_id(
    trade: Trade,
    *,
    timeout: float = 2.0,
    poll_interval: float = 0.05,
) -> Optional[int]:
    if trade.order.orderId:
        return trade.order.orderId
    deadline = time.time() + timeout
    while time.time() < deadline:
        if trade.order.orderId:
            return trade.order.orderId
        await asyncio.sleep(poll_interval)
    return trade.order.orderId or None


async def _wait_for_order_status(
    trade: Trade,
    *,
    timeout: float = 2.0,
    poll_interval: float = 0.1,
) -> Optional[str]:
    status = trade.orderStatus.status
    deadline = time.time() + timeout
    while not status and time.time() < deadline:
        await asyncio.sleep(poll_interval)
        status = trade.orderStatus.status
    return status or None


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

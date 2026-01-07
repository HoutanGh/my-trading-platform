from __future__ import annotations

import asyncio
import time
from typing import Optional

from ib_insync import IB, LimitOrder, MarketOrder, Stock, Trade

from appsv2.adapters.broker.ibkr_connection import IBKRConnection
from appsv2.core.orders.models import OrderAck, OrderSpec, OrderType
from appsv2.core.orders.ports import OrderPort


class IBKROrderPort(OrderPort):
    def __init__(self, connection: IBKRConnection) -> None:
        self._connection = connection
        self._ib: IB = connection.ib

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
        order_id = await _wait_for_order_id(trade)
        status = await _wait_for_order_status(trade)
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

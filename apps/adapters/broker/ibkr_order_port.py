from __future__ import annotations

import asyncio
import copy
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from ib_insync import IB, LimitOrder, MarketOrder, Stock, StopLimitOrder, Trade

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.orders.events import (
    BracketChildOrderFilled,
    BracketChildOrderStatusChanged,
    OrderIdAssigned,
    OrderSent,
    OrderStatusChanged,
    OrderFilled,
)
from apps.core.orders.models import (
    BracketOrderSpec,
    LadderOrderSpec,
    OrderAck,
    OrderCancelSpec,
    OrderReplaceSpec,
    OrderSide,
    OrderSpec,
    OrderType,
)
from apps.core.orders.ports import EventBus, OrderPort

_BRACKET_STOP_LIMIT_OFFSET = 0.02


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
            _attach_trade_handlers(trade, spec, self._event_bus)
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

    async def cancel_order(self, spec: OrderCancelSpec) -> OrderAck:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")
        trade = _find_trade_by_order_id(self._ib, spec.order_id)
        if trade is None:
            raise RuntimeError(f"Order {spec.order_id} not found in current session")
        self._ib.cancelOrder(trade.order)
        status = await _wait_for_order_status(trade)
        return OrderAck.now(order_id=spec.order_id, status=status)

    async def replace_order(self, spec: OrderReplaceSpec) -> OrderAck:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")
        trade = _find_trade_by_order_id(self._ib, spec.order_id)
        if trade is None:
            raise RuntimeError(f"Order {spec.order_id} not found in current session")
        order = copy.copy(trade.order)
        order_type = str(getattr(order, "orderType", "")).strip().upper()
        if order_type not in {"LMT", "LIMIT"}:
            raise RuntimeError("Only limit orders can be replaced")
        if spec.qty is not None:
            order.totalQuantity = spec.qty
        if spec.limit_price is not None:
            order.lmtPrice = spec.limit_price
        if spec.tif is not None:
            order.tif = spec.tif
        if spec.outside_rth is not None:
            order.outsideRth = spec.outside_rth
        order.orderId = trade.order.orderId
        updated_trade = self._ib.placeOrder(trade.contract, order)
        if self._event_bus and updated_trade is not trade:
            _attach_trade_handlers(updated_trade, _order_spec_from_trade(updated_trade), self._event_bus)
        status = await _wait_for_order_status(updated_trade)
        return OrderAck.now(order_id=spec.order_id, status=status)

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

        child_side = OrderSide.SELL if spec.side == OrderSide.BUY else OrderSide.BUY
        oca_group = f"BRKT-{uuid.uuid4().hex[:10]}"
        take_profit = LimitOrder(child_side.value, spec.qty, spec.take_profit, tif=spec.tif)
        take_profit.ocaGroup = oca_group
        take_profit.transmit = False
        take_profit.outsideRth = spec.outside_rth
        if spec.account:
            take_profit.account = spec.account
        if spec.client_tag:
            take_profit.orderRef = spec.client_tag

        stop_loss = _build_stop_limit_order(
            side=child_side,
            qty=spec.qty,
            stop_price=spec.stop_loss,
            offset=_BRACKET_STOP_LIMIT_OFFSET,
            tif=spec.tif,
            outside_rth=spec.outside_rth,
            account=spec.account,
            client_tag=spec.client_tag,
        )
        stop_loss.ocaGroup = oca_group
        stop_loss.transmit = True

        parent_spec = _entry_spec_from_bracket(spec)
        trade = self._ib.placeOrder(qualified, parent)
        if self._event_bus:
            _attach_trade_handlers(trade, parent_spec, self._event_bus)
        if self._event_bus:
            self._event_bus.publish(OrderSent.now(parent_spec))
        order_id = await _wait_for_order_id(trade)
        take_profit.parentId = order_id
        stop_loss.parentId = order_id
        tp_trade = self._ib.placeOrder(qualified, take_profit)
        sl_trade = self._ib.placeOrder(qualified, stop_loss)
        if self._event_bus:
            _attach_bracket_child_handlers(
                tp_trade,
                kind="take_profit",
                symbol=spec.symbol,
                side=child_side,
                qty=spec.qty,
                price=spec.take_profit,
                parent_order_id=order_id,
                client_tag=spec.client_tag,
                event_bus=self._event_bus,
            )
            _attach_bracket_child_handlers(
                sl_trade,
                kind="stop_loss",
                symbol=spec.symbol,
                side=child_side,
                qty=spec.qty,
                price=spec.stop_loss,
                parent_order_id=order_id,
                client_tag=spec.client_tag,
                event_bus=self._event_bus,
            )
        if self._event_bus:
            self._event_bus.publish(OrderIdAssigned.now(parent_spec, order_id))
        status = await _wait_for_order_status(trade)
        if self._event_bus:
            self._event_bus.publish(
                OrderStatusChanged.now(parent_spec, order_id=order_id, status=status)
            )
        return OrderAck.now(order_id=order_id, status=status)

    async def submit_ladder_order(self, spec: LadderOrderSpec) -> OrderAck:
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

        child_side = OrderSide.SELL if spec.side == OrderSide.BUY else OrderSide.BUY

        parent_spec = _entry_spec_from_ladder(spec)
        trade = self._ib.placeOrder(qualified, parent)
        if self._event_bus:
            _attach_trade_handlers(trade, parent_spec, self._event_bus)
        if self._event_bus:
            self._event_bus.publish(OrderSent.now(parent_spec))
        order_id = await _wait_for_order_id(trade)

        tp_states: list[_LadderTakeProfitState] = []
        for idx, (tp_price, tp_qty) in enumerate(
            zip(spec.take_profits, spec.take_profit_qtys), start=1
        ):
            take_profit = LimitOrder(child_side.value, tp_qty, tp_price, tif=spec.tif)
            take_profit.parentId = order_id
            take_profit.transmit = False
            take_profit.outsideRth = spec.outside_rth
            if spec.account:
                take_profit.account = spec.account
            if spec.client_tag:
                take_profit.orderRef = spec.client_tag
            tp_trade = self._ib.placeOrder(qualified, take_profit)
            tp_states.append(
                _LadderTakeProfitState(index=idx, qty=tp_qty, price=tp_price, trade=tp_trade)
            )
            if self._event_bus:
                _attach_bracket_child_handlers(
                    tp_trade,
                    kind=f"take_profit_{idx}",
                    symbol=spec.symbol,
                    side=child_side,
                    qty=tp_qty,
                    price=tp_price,
                    parent_order_id=order_id,
                    client_tag=spec.client_tag,
                    event_bus=self._event_bus,
                )

        stop_order = _build_stop_limit_order(
            side=child_side,
            qty=spec.qty,
            stop_price=spec.stop_loss,
            offset=spec.stop_limit_offset,
            tif=spec.tif,
            outside_rth=spec.outside_rth,
            account=spec.account,
            client_tag=spec.client_tag,
        )
        stop_order.parentId = order_id
        stop_order.transmit = True
        stop_trade = self._ib.placeOrder(qualified, stop_order)
        if self._event_bus:
            _attach_bracket_child_handlers(
                stop_trade,
                kind="stop_loss",
                symbol=spec.symbol,
                side=child_side,
                qty=spec.qty,
                price=spec.stop_loss,
                parent_order_id=order_id,
                client_tag=spec.client_tag,
                event_bus=self._event_bus,
            )

        manager = _LadderStopManager(
            ib=self._ib,
            contract=qualified,
            symbol=spec.symbol,
            child_side=child_side,
            parent_order_id=order_id,
            tp_states=tp_states,
            stop_trade=stop_trade,
            stop_price=spec.stop_loss,
            stop_qty=spec.qty,
            stop_updates=spec.stop_updates,
            stop_limit_offset=spec.stop_limit_offset,
            tif=spec.tif,
            outside_rth=spec.outside_rth,
            account=spec.account,
            client_tag=spec.client_tag,
            event_bus=self._event_bus,
        )
        for state in tp_states:
            _attach_ladder_tp_manager(state.trade, tp_index=state.index, manager=manager)
        _attach_ladder_stop_manager(stop_trade, manager=manager)

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


def _find_trade_by_order_id(ib: IB, order_id: int) -> Optional[Trade]:
    for trade in ib.trades():
        if getattr(getattr(trade, "order", None), "orderId", None) == order_id:
            return trade
    return None


def _order_spec_from_trade(trade: Trade) -> OrderSpec:
    order = trade.order
    contract = trade.contract
    symbol = getattr(contract, "symbol", "") or ""
    exchange = getattr(contract, "exchange", None) or "SMART"
    currency = getattr(contract, "currency", None) or "USD"
    action = str(getattr(order, "action", "")).strip().upper()
    side = OrderSide.SELL if action == "SELL" else OrderSide.BUY
    order_type_raw = str(getattr(order, "orderType", "")).strip().upper()
    if order_type_raw in {"LMT", "LIMIT"}:
        order_type = OrderType.LIMIT
    else:
        order_type = OrderType.MARKET
    limit_price = getattr(order, "lmtPrice", None)
    tif = getattr(order, "tif", None) or "DAY"
    outside_rth = bool(getattr(order, "outsideRth", False))
    account = getattr(order, "account", None) or None
    client_tag = getattr(order, "orderRef", None) or None
    qty_raw = getattr(order, "totalQuantity", 0)
    try:
        qty = int(qty_raw)
    except (TypeError, ValueError):
        qty = 0
    return OrderSpec(
        symbol=symbol,
        qty=qty,
        side=side,
        order_type=order_type,
        limit_price=limit_price,
        tif=str(tif),
        outside_rth=outside_rth,
        exchange=exchange,
        currency=currency,
        account=account,
        client_tag=client_tag,
    )


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


def _entry_spec_from_ladder(spec: LadderOrderSpec) -> OrderSpec:
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


@dataclass
class _LadderTakeProfitState:
    index: int
    qty: int
    price: float
    trade: Trade


class _LadderStopManager:
    def __init__(
        self,
        *,
        ib: IB,
        contract: Stock,
        symbol: str,
        child_side: OrderSide,
        parent_order_id: Optional[int],
        tp_states: list[_LadderTakeProfitState],
        stop_trade: Trade,
        stop_price: float,
        stop_qty: int,
        stop_updates: list[float],
        stop_limit_offset: float,
        tif: str,
        outside_rth: bool,
        account: Optional[str],
        client_tag: Optional[str],
        event_bus: EventBus | None,
    ) -> None:
        self._ib = ib
        self._contract = contract
        self._symbol = symbol
        self._child_side = child_side
        self._parent_order_id = parent_order_id
        self._tp_states = {state.index: state for state in tp_states}
        self._stop_trade = stop_trade
        self._stop_price = stop_price
        self._stop_qty = stop_qty
        self._stop_updates = stop_updates
        self._stop_limit_offset = stop_limit_offset
        self._tif = tif
        self._outside_rth = outside_rth
        self._account = account
        self._client_tag = client_tag
        self._event_bus = event_bus
        self._lock = threading.Lock()
        self._processed_tps: set[int] = set()
        self._filled_tp_count = 0
        self._total_qty = float(stop_qty)
        self._remaining_qty = float(stop_qty)
        self._tp_filled: dict[int, float] = {}
        self._stop_filled = False

    def handle_tp_fill(self, tp_index: int, trade_obj: Trade) -> None:
        with self._lock:
            if self._stop_filled:
                return
            state = self._tp_states.get(tp_index)
            if not state:
                return
            order_status = getattr(trade_obj, "orderStatus", None)
            if not order_status:
                return
            filled_qty = _maybe_float(getattr(order_status, "filled", None))
            if filled_qty is None:
                return
            prev_filled = self._tp_filled.get(tp_index, 0.0)
            if filled_qty <= prev_filled:
                return
            self._tp_filled[tp_index] = filled_qty
            new_remaining = self._total_qty - sum(self._tp_filled.values())
            if new_remaining < 0:
                new_remaining = 0.0

            stop_price = self._stop_price
            stop_price_changed = False
            if tp_index not in self._processed_tps and filled_qty >= state.qty:
                self._processed_tps.add(tp_index)
                self._filled_tp_count += 1
                if self._filled_tp_count <= len(self._stop_updates):
                    stop_price = self._stop_updates[self._filled_tp_count - 1]
                    stop_price_changed = True

            self._remaining_qty = new_remaining
            if self._remaining_qty <= 0:
                self._cancel_stop()
                return
            remaining_qty = int(round(self._remaining_qty))
            if stop_price_changed or remaining_qty != self._stop_qty:
                self._replace_stop(stop_price, remaining_qty)

    def handle_stop_fill(self, trade_obj: Trade) -> None:
        with self._lock:
            if self._stop_filled:
                return
            if not _has_any_fill(trade_obj):
                return
            self._stop_filled = True
            self._remaining_qty = 0
            for tp_index, state in list(self._tp_states.items()):
                if tp_index in self._processed_tps:
                    continue
                self._ib.cancelOrder(state.trade.order)

    def _cancel_stop(self) -> None:
        if self._stop_trade:
            self._ib.cancelOrder(self._stop_trade.order)
            self._stop_trade = None

    def _replace_stop(self, stop_price: float, qty: int) -> None:
        if self._stop_trade:
            self._ib.cancelOrder(self._stop_trade.order)
        stop_order = _build_stop_limit_order(
            side=self._child_side,
            qty=qty,
            stop_price=stop_price,
            offset=self._stop_limit_offset,
            tif=self._tif,
            outside_rth=self._outside_rth,
            account=self._account,
            client_tag=self._client_tag,
        )
        stop_order.parentId = self._parent_order_id
        stop_order.transmit = True
        stop_trade = self._ib.placeOrder(self._contract, stop_order)
        self._stop_trade = stop_trade
        self._stop_qty = qty
        self._stop_price = stop_price
        if self._event_bus:
            _attach_bracket_child_handlers(
                stop_trade,
                kind="stop_loss",
                symbol=self._symbol,
                side=self._child_side,
                qty=qty,
                price=stop_price,
                parent_order_id=self._parent_order_id,
                client_tag=self._client_tag,
                event_bus=self._event_bus,
            )
        _attach_ladder_stop_manager(stop_trade, manager=self)


def _build_stop_limit_order(
    *,
    side: OrderSide,
    qty: int,
    stop_price: float,
    offset: float,
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    client_tag: Optional[str],
) -> StopLimitOrder:
    limit_price = _stop_limit_price(side, stop_price, offset)
    order = StopLimitOrder(side.value, qty, limit_price, stop_price, tif=tif)
    order.lmtPrice = limit_price
    order.auxPrice = stop_price
    order.outsideRth = outside_rth
    if account:
        order.account = account
    if client_tag:
        order.orderRef = client_tag
    return order


def _stop_limit_price(side: OrderSide, stop_price: float, offset: float) -> float:
    if side == OrderSide.SELL:
        return stop_price - offset
    return stop_price + offset


def _is_trade_filled(trade_obj: Trade, expected_qty: int) -> bool:
    order_status = getattr(trade_obj, "orderStatus", None)
    if not order_status:
        return False
    status = getattr(order_status, "status", None)
    if status and str(status).strip().lower() == "filled":
        return True
    remaining = _maybe_float(getattr(order_status, "remaining", None))
    if remaining is not None and remaining <= 0:
        return True
    filled = _maybe_float(getattr(order_status, "filled", None))
    if filled is not None and filled >= expected_qty:
        return True
    return False


def _has_any_fill(trade_obj: Trade) -> bool:
    order_status = getattr(trade_obj, "orderStatus", None)
    if not order_status:
        return False
    filled = _maybe_float(getattr(order_status, "filled", None))
    if filled is not None and filled > 0:
        return True
    status = getattr(order_status, "status", None)
    if not status:
        return False
    normalized = str(status).strip().lower()
    return normalized in {"filled", "partiallyfilled", "partially_filled"}


def _attach_trade_handlers(trade: Trade, spec: OrderSpec, event_bus: EventBus) -> None:
    last_status: Optional[str] = None
    last_fill: tuple[Optional[float], Optional[float], Optional[float], Optional[str]] | None = None

    def _publish_status(trade_obj: Trade) -> None:
        nonlocal last_status
        status = trade_obj.orderStatus.status
        if not status or status == last_status:
            return
        last_status = status
        event_bus.publish(
            OrderStatusChanged.now(
                spec,
                order_id=trade_obj.order.orderId,
                status=status,
            )
        )

    def _publish_fill(trade_obj: Trade) -> None:
        order_status = trade_obj.orderStatus
        filled_qty = _maybe_float(order_status.filled)
        avg_fill_price = _maybe_float(order_status.avgFillPrice)
        remaining_qty = _maybe_float(order_status.remaining)
        status = order_status.status
        nonlocal last_fill
        snapshot = (filled_qty, avg_fill_price, remaining_qty, status)
        if snapshot == last_fill:
            return
        last_fill = snapshot
        event_bus.publish(
            OrderFilled.now(
                spec,
                order_id=trade_obj.order.orderId,
                status=status,
                filled_qty=filled_qty,
                avg_fill_price=avg_fill_price,
                remaining_qty=remaining_qty,
            )
        )

    status_event = getattr(trade, "statusEvent", None)
    if status_event is not None:
        status_event += lambda trade_obj, *_args: _publish_status(trade_obj)

    filled_event = getattr(trade, "filledEvent", None)
    if filled_event is not None:
        filled_event += lambda trade_obj, *_args: _publish_fill(trade_obj)

    fill_event = getattr(trade, "fillEvent", None)
    if fill_event is not None:
        fill_event += lambda trade_obj, *_args: _publish_fill(trade_obj)


def _attach_bracket_child_handlers(
    trade: Trade,
    *,
    kind: str,
    symbol: str,
    side: OrderSide,
    qty: int,
    price: float,
    parent_order_id: Optional[int],
    client_tag: Optional[str],
    event_bus: EventBus,
) -> None:
    last_status: Optional[str] = None
    last_fill: tuple[Optional[float], Optional[float], Optional[float], Optional[str]] | None = None

    def _publish_status(trade_obj: Trade) -> None:
        nonlocal last_status
        status = trade_obj.orderStatus.status
        if not status or status == last_status:
            return
        last_status = status
        event_bus.publish(
            BracketChildOrderStatusChanged.now(
                kind=kind,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                order_id=trade_obj.order.orderId,
                parent_order_id=parent_order_id,
                status=status,
                client_tag=client_tag,
            )
        )

    def _publish_fill(trade_obj: Trade) -> None:
        order_status = trade_obj.orderStatus
        filled_qty = _maybe_float(order_status.filled)
        avg_fill_price = _maybe_float(order_status.avgFillPrice)
        remaining_qty = _maybe_float(order_status.remaining)
        status = order_status.status
        nonlocal last_fill
        snapshot = (filled_qty, avg_fill_price, remaining_qty, status)
        if snapshot == last_fill:
            return
        last_fill = snapshot
        event_bus.publish(
            BracketChildOrderFilled.now(
                kind=kind,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                order_id=trade_obj.order.orderId,
                parent_order_id=parent_order_id,
                status=status,
                filled_qty=filled_qty,
                avg_fill_price=avg_fill_price,
                remaining_qty=remaining_qty,
                client_tag=client_tag,
            )
        )

    status_event = getattr(trade, "statusEvent", None)
    if status_event is not None:
        status_event += lambda trade_obj, *_args: _publish_status(trade_obj)

    filled_event = getattr(trade, "filledEvent", None)
    if filled_event is not None:
        filled_event += lambda trade_obj, *_args: _publish_fill(trade_obj)

    fill_event = getattr(trade, "fillEvent", None)
    if fill_event is not None:
        fill_event += lambda trade_obj, *_args: _publish_fill(trade_obj)


def _attach_ladder_tp_manager(trade: Trade, *, tp_index: int, manager: _LadderStopManager) -> None:
    filled_event = getattr(trade, "filledEvent", None)
    if filled_event is not None:
        filled_event += lambda trade_obj, *_args: manager.handle_tp_fill(tp_index, trade_obj)

    fill_event = getattr(trade, "fillEvent", None)
    if fill_event is not None:
        fill_event += lambda trade_obj, *_args: manager.handle_tp_fill(tp_index, trade_obj)


def _attach_ladder_stop_manager(trade: Trade, *, manager: _LadderStopManager) -> None:
    filled_event = getattr(trade, "filledEvent", None)
    if filled_event is not None:
        filled_event += lambda trade_obj, *_args: manager.handle_stop_fill(trade_obj)

    fill_event = getattr(trade, "fillEvent", None)
    if fill_event is not None:
        fill_event += lambda trade_obj, *_args: manager.handle_stop_fill(trade_obj)


def _maybe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

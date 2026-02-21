from __future__ import annotations

import asyncio
import copy
import math
import os
import threading
import time
import uuid
from concurrent.futures import CancelledError as ThreadFutureCancelledError
from concurrent.futures import Future as ThreadFuture
from dataclasses import replace
from collections.abc import Coroutine
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Optional, cast

from apps.adapters.broker._ib_client import (
    IB,
    LimitOrder,
    MarketOrder,
    Stock,
    StopLimitOrder,
    StopOrder,
    Trade,
    UNSET_DOUBLE,
)
from apps.adapters.broker._ib_compat import attach_trade_events, req_tickers_snapshot

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.adapters.broker.ibkr_session_phase import IBKRSessionPhaseResolver, SessionPhase
from apps.core.orders.events import (
    BracketChildOrderFilled,
    BracketChildOrderBrokerSnapshot,
    BracketChildQuantityMismatchDetected,
    BracketChildOrderStatusChanged,
    LadderProtectionStateChanged,
    LadderStopLossReplaceFailed,
    LadderStopLossReplaced,
    OrderStopModeSelected,
    OrderIdAssigned,
    OrderSent,
    OrderStatusChanged,
    OrderFilled,
)
from apps.core.orders.models import (
    BracketOrderSpec,
    LadderExecutionMode,
    LadderOrderSpec,
    OrderAck,
    OrderCancelSpec,
    OrderReplaceSpec,
    OrderSide,
    OrderSpec,
    OrderType,
)
from apps.core.orders.ports import EventBus, OrderPort
from apps.core.orders.detached_ladder import (
    DetachedRepriceMilestone,
    collect_detached_reprice_decisions,
    select_detached_incident_pair,
)

_INACTIVE_ORDER_STATUSES = {"inactive", "cancelled", "apicancelled", "filled"}
_ScheduledHandle = asyncio.Task[None] | ThreadFuture[None]


class IBKROrderPort(OrderPort):
    def __init__(self, connection: IBKRConnection, event_bus: EventBus | None = None) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._event_bus = event_bus
        self._outside_rth_stop_limit_buffer_pct = _outside_rth_stop_limit_buffer_pct_from_env()
        self._session_phase_resolver = IBKRSessionPhaseResolver(
            self._ib,
            event_bus=event_bus,
        )
        self._scheduled_handles: set[_ScheduledHandle] = set()
        self._scheduled_handles_lock = threading.Lock()
        self._scheduler_closed = False

    async def prewarm_session_phase(
        self,
        *,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> None:
        await self._session_phase_resolver.prewarm(
            symbol=symbol,
            exchange=exchange,
            currency=currency,
        )

    def clear_session_phase_cache(self) -> None:
        self._session_phase_resolver.clear_cache()

    def close(self) -> None:
        handles: list[_ScheduledHandle]
        with self._scheduled_handles_lock:
            self._scheduler_closed = True
            handles = list(self._scheduled_handles)
            self._scheduled_handles.clear()
        for handle in handles:
            handle.cancel()

    def _schedule_managed_coroutine(
        self,
        loop: asyncio.AbstractEventLoop,
        coro: Coroutine[object, object, None],
    ) -> Optional[_ScheduledHandle]:
        with self._scheduled_handles_lock:
            if self._scheduler_closed:
                coro.close()
                return None
        handle = _schedule_coroutine(loop, coro)
        if handle is None:
            return None

        def _release(done_handle: _ScheduledHandle) -> None:
            with self._scheduled_handles_lock:
                self._scheduled_handles.discard(done_handle)

        with self._scheduled_handles_lock:
            if self._scheduler_closed:
                handle.cancel()
                return None
            self._scheduled_handles.add(handle)
        handle.add_done_callback(_release)
        return handle

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

        trade = _place_order_sanitized(self._ib, qualified, order)
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
        timeout = max(self._connection.config.timeout, 1.0)
        trade = await _find_trade_by_order_id_with_refresh(
            self._ib,
            spec.order_id,
            timeout=timeout,
        )
        if trade is None:
            raise RuntimeError(f"Order {spec.order_id} not found in broker open orders")
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
        updated_trade = _place_order_sanitized(self._ib, trade.contract, order)
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
        loop = asyncio.get_running_loop()
        session_phase = self._session_phase_resolver.resolve_phase(qualified)
        spec = replace(spec, outside_rth=_outside_rth_for_session_phase(session_phase))

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

        use_stop_limit = _uses_stop_limit(spec.outside_rth)
        stop_loss = _build_protective_stop_order(
            side=child_side,
            qty=spec.qty,
            stop_price=spec.stop_loss,
            limit_price=spec.stop_loss,
            tif=spec.tif,
            outside_rth=spec.outside_rth,
            account=spec.account,
            client_tag=spec.client_tag,
            use_stop_limit=use_stop_limit,
            stop_limit_buffer_pct=self._outside_rth_stop_limit_buffer_pct,
        )
        stop_loss.ocaGroup = oca_group
        stop_loss.transmit = True

        parent_spec = _entry_spec_from_bracket(spec)
        trade = _place_order_sanitized(self._ib, qualified, parent)
        if self._event_bus:
            _attach_trade_handlers(trade, parent_spec, self._event_bus)
        if self._event_bus:
            self._event_bus.publish(OrderSent.now(parent_spec))
        order_id = await _wait_for_order_id(trade)
        take_profit.parentId = order_id
        stop_loss.parentId = order_id
        tp_trade = _place_order_sanitized(self._ib, qualified, take_profit)
        sl_trade = _place_order_sanitized(self._ib, qualified, stop_loss)
        _publish_stop_mode_selected(
            event_bus=self._event_bus,
            symbol=spec.symbol,
            parent_order_id=order_id,
            trade=sl_trade,
            session_phase=session_phase,
            stop_price=spec.stop_loss,
            execution_mode="attached",
            client_tag=spec.client_tag,
        )
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
        if use_stop_limit:
            _attach_stop_trigger_reprice(
                sl_trade,
                ib=self._ib,
                contract=qualified,
                side=child_side,
                qty=spec.qty,
                stop_price=spec.stop_loss,
                symbol=spec.symbol,
                parent_order_id=order_id,
                client_tag=spec.client_tag,
                event_bus=self._event_bus,
                loop=loop,
                scheduler=self._schedule_managed_coroutine,
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
        loop = asyncio.get_running_loop()
        session_phase = self._session_phase_resolver.resolve_phase(qualified)
        spec = replace(spec, outside_rth=_outside_rth_for_session_phase(session_phase))

        if spec.execution_mode == LadderExecutionMode.ATTACHED:
            raise RuntimeError(
                "Ladder ATTACHED mode is not supported; use bracket for 1 TP + 1 SL."
            )
        if spec.execution_mode in {LadderExecutionMode.DETACHED, LadderExecutionMode.DETACHED_70_30}:
            return await self._submit_ladder_order_detached(
                spec=spec,
                qualified=qualified,
                loop=loop,
            )
        raise RuntimeError(f"Unsupported ladder execution mode: {spec.execution_mode}")

    async def _submit_ladder_order_detached(
        self,
        *,
        spec: LadderOrderSpec,
        qualified: Stock,
        loop: asyncio.AbstractEventLoop,
    ) -> OrderAck:
        if spec.execution_mode == LadderExecutionMode.DETACHED_70_30:
            return await self._submit_ladder_order_detached_70_30(
                spec=spec,
                qualified=qualified,
                loop=loop,
            )
        if spec.execution_mode == LadderExecutionMode.DETACHED and len(spec.take_profits) == 3:
            return await self._submit_ladder_order_detached_3_pairs(
                spec=spec,
                qualified=qualified,
                loop=loop,
            )
        raise RuntimeError(
            "Unsupported detached ladder config. Use detached70 for 2 TP or detached for 3 TP."
        )

    def _build_detached_parent_order(self, spec: LadderOrderSpec) -> object:
        if spec.entry_type == OrderType.MARKET:
            parent = MarketOrder(spec.side.value, spec.qty, tif=spec.tif)
        elif spec.entry_type == OrderType.LIMIT:
            parent = LimitOrder(spec.side.value, spec.qty, spec.entry_price, tif=spec.tif)
        else:
            raise RuntimeError(f"Unsupported entry type: {spec.entry_type}")
        parent.transmit = True
        parent.outsideRth = spec.outside_rth
        if spec.account:
            parent.account = spec.account
        if spec.client_tag:
            parent.orderRef = spec.client_tag
        return parent

    async def _submit_detached_parent_entry(
        self,
        *,
        spec: LadderOrderSpec,
        qualified: Stock,
    ) -> tuple[Trade, Optional[int], Optional[str]]:
        parent = self._build_detached_parent_order(spec)
        parent_spec = _entry_spec_from_ladder(spec)
        trade = _place_order_sanitized(self._ib, qualified, parent)
        if self._event_bus:
            _attach_trade_handlers(trade, parent_spec, self._event_bus)
            self._event_bus.publish(OrderSent.now(parent_spec))

        order_id = await _wait_for_order_id(trade)
        if self._event_bus:
            self._event_bus.publish(OrderIdAssigned.now(parent_spec, order_id))

        status = await _wait_for_order_status(trade)
        if self._event_bus:
            self._event_bus.publish(
                OrderStatusChanged.now(parent_spec, order_id=order_id, status=status)
            )
        return trade, order_id, status

    async def _ensure_detached_inventory_confirmed(
        self,
        *,
        spec: LadderOrderSpec,
        trade: Trade,
        qualified: Stock,
        mode_label: str,
    ) -> None:
        confirmed, filled_qty, position_qty = await _wait_for_inventory_confirmation(
            ib=self._ib,
            trade=trade,
            contract=qualified,
            account=spec.account,
            expected_qty=spec.qty,
            timeout=max(self._connection.config.timeout * 4.0, 4.0),
        )
        if confirmed:
            return
        if self._connection.config.paper_only and filled_qty > 0:
            await _flatten_position_market(
                ib=self._ib,
                contract=qualified,
                side=OrderSide.SELL,
                qty=int(round(filled_qty)),
                tif=spec.tif,
                outside_rth=spec.outside_rth,
                account=spec.account,
                client_tag=spec.client_tag,
            )
        raise RuntimeError(
            f"{mode_label} exits were not armed because inventory confirmation failed "
            f"(filled={filled_qty}, position={position_qty})."
        )

    async def _submit_ladder_order_detached_70_30(
        self,
        *,
        spec: LadderOrderSpec,
        qualified: Stock,
        loop: asyncio.AbstractEventLoop,
    ) -> OrderAck:
        trade, order_id, status = await self._submit_detached_parent_entry(
            spec=spec,
            qualified=qualified,
        )
        await self._ensure_detached_inventory_confirmed(
            spec=spec,
            trade=trade,
            qualified=qualified,
            mode_label="Detached 70/30",
        )

        child_side = OrderSide.SELL if spec.side == OrderSide.BUY else OrderSide.BUY
        use_stop_limit = _uses_stop_limit(spec.outside_rth)
        replace_timeout = max(self._connection.config.timeout, 1.0)
        exit_oca_type = 2
        pair_oca_group = {
            1: f"DET7030A-{uuid.uuid4().hex[:10]}",
            2: f"DET7030B-{uuid.uuid4().hex[:10]}",
        }
        pair_stop_price = {
            1: float(spec.stop_loss),
            2: float(spec.stop_loss),
        }
        pair_tp_price = {
            1: float(spec.take_profits[0]),
            2: float(spec.take_profits[1]),
        }
        pair_tp_qty = {
            1: int(spec.take_profit_qtys[0]),
            2: int(spec.take_profit_qtys[1]),
        }
        stop_update_price = (
            _snap_price(float(spec.stop_updates[0]), contract=qualified)
            if spec.stop_updates
            else _snap_price(float(spec.stop_loss), contract=qualified)
        )

        stop_trades: dict[int, Trade] = {}
        tp_trades: dict[int, Trade] = {}

        for pair_index in (1, 2):
            qty = pair_tp_qty[pair_index]
            stop_price = pair_stop_price[pair_index]
            oca_group = pair_oca_group[pair_index]

            def _stop_factory(
                *,
                pair_qty: int = qty,
                pair_stop_price: float = stop_price,
                group: str = oca_group,
            ) -> object:
                stop_order = _build_protective_stop_order(
                    side=child_side,
                    qty=pair_qty,
                    stop_price=pair_stop_price,
                    limit_price=pair_stop_price,
                    tif=spec.tif,
                    outside_rth=spec.outside_rth,
                    account=spec.account,
                    client_tag=spec.client_tag,
                    use_stop_limit=use_stop_limit,
                    stop_limit_buffer_pct=self._outside_rth_stop_limit_buffer_pct,
                )
                _apply_oca(stop_order, group=group, oca_type=exit_oca_type)
                stop_order.parentId = 0
                stop_order.transmit = True
                return stop_order

            stop_trade = await _place_order_with_reconcile(
                ib=self._ib,
                contract=qualified,
                order_factory=_stop_factory,
                expected_qty=qty,
                expected_parent_id=0,
                expected_oca_group=oca_group,
                expected_oca_type=exit_oca_type,
                timeout=replace_timeout,
            )
            stop_trades[pair_index] = stop_trade
            _publish_stop_mode_selected(
                event_bus=self._event_bus,
                symbol=spec.symbol,
                parent_order_id=order_id,
                trade=stop_trade,
                session_phase=SessionPhase.RTH if not spec.outside_rth else SessionPhase.OUTSIDE_RTH,
                stop_price=stop_price,
                execution_mode="detached70",
                client_tag=spec.client_tag,
            )
            if self._event_bus:
                _attach_bracket_child_handlers(
                    stop_trade,
                    kind=f"det70_stop_{pair_index}",
                    symbol=spec.symbol,
                    side=child_side,
                    qty=qty,
                    price=stop_price,
                    parent_order_id=order_id,
                    client_tag=spec.client_tag,
                    event_bus=self._event_bus,
                )

            tp_price = pair_tp_price[pair_index]

            def _tp_factory(
                *,
                pair_qty: int = qty,
                pair_tp_price: float = tp_price,
                group: str = oca_group,
            ) -> object:
                tp_order = LimitOrder(child_side.value, pair_qty, pair_tp_price, tif=spec.tif)
                _apply_oca(tp_order, group=group, oca_type=exit_oca_type)
                tp_order.parentId = 0
                tp_order.transmit = True
                tp_order.outsideRth = spec.outside_rth
                if spec.account:
                    tp_order.account = spec.account
                if spec.client_tag:
                    tp_order.orderRef = spec.client_tag
                return tp_order

            tp_trade = await _place_order_with_reconcile(
                ib=self._ib,
                contract=qualified,
                order_factory=_tp_factory,
                expected_qty=qty,
                expected_parent_id=0,
                expected_oca_group=oca_group,
                expected_oca_type=exit_oca_type,
                timeout=replace_timeout,
            )
            tp_trades[pair_index] = tp_trade
            if self._event_bus:
                _attach_bracket_child_handlers(
                    tp_trade,
                    kind=f"det70_tp_{pair_index}",
                    symbol=spec.symbol,
                    side=child_side,
                    qty=qty,
                    price=tp_price,
                    parent_order_id=order_id,
                    client_tag=spec.client_tag,
                    event_bus=self._event_bus,
                )

        state_lock = threading.Lock()
        tp_exec_ids: dict[int, set[str]] = {1: set(), 2: set()}
        tp_filled_qty: dict[int, float] = {1: 0.0, 2: 0.0}
        tp_completed: dict[int, bool] = {1: False, 2: False}
        stop_filled: dict[int, bool] = {1: False, 2: False}
        protection_state: Optional[str] = None
        incident_pairs_active: set[int] = set()
        incident_pairs_inflight: set[int] = set()
        reprice_milestones = (
            DetachedRepriceMilestone(
                required_pairs=frozenset({1}),
                target_pairs=(2,),
                stop_price=stop_update_price,
            ),
        )
        milestone_applied = [False] * len(reprice_milestones)
        leg_by_order_id: dict[int, tuple[str, int]] = {}
        gateway_unsubscribe_ref: dict[str, Optional[Callable[[], None]]] = {"fn": None}

        def _register_leg_locked(*, kind: str, pair_index: int, trade_obj: Optional[Trade]) -> None:
            order_id_value = _trade_order_id(trade_obj)
            if order_id_value is None:
                return
            leg_by_order_id[order_id_value] = (kind, pair_index)

        def _refresh_leg_registry_locked() -> None:
            leg_by_order_id.clear()
            for idx, trade_obj in stop_trades.items():
                _register_leg_locked(kind="stop", pair_index=idx, trade_obj=trade_obj)
            for idx, trade_obj in tp_trades.items():
                _register_leg_locked(kind="tp", pair_index=idx, trade_obj=trade_obj)

        def _active_tp_order_ids_locked() -> list[int]:
            ids: list[int] = []
            for idx, trade_obj in tp_trades.items():
                order_id_value = _trade_order_id(trade_obj)
                if order_id_value is None:
                    continue
                status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
                if status_value in _INACTIVE_ORDER_STATUSES and not tp_completed[idx]:
                    continue
                if tp_completed[idx] and status_value in _INACTIVE_ORDER_STATUSES:
                    continue
                ids.append(order_id_value)
            ids.sort()
            return ids

        def _current_stop_order_id_locked() -> Optional[int]:
            for idx in (2, 1):
                trade_obj = stop_trades.get(idx)
                if trade_obj is None:
                    continue
                status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
                if status_value in _INACTIVE_ORDER_STATUSES:
                    continue
                order_id_value = _trade_order_id(trade_obj)
                if order_id_value is not None:
                    return order_id_value
            for idx in (2, 1):
                order_id_value = _trade_order_id(stop_trades.get(idx))
                if order_id_value is not None:
                    return order_id_value
            return None

        def _active_stop_remaining_qty_locked(pair_index: int) -> int:
            trade_obj = stop_trades.get(pair_index)
            if trade_obj is None:
                return 0
            status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
            if status_value in _INACTIVE_ORDER_STATUSES:
                return 0
            remaining_qty = _maybe_float(getattr(trade_obj.orderStatus, "remaining", None))
            if remaining_qty is None:
                remaining_qty = _maybe_float(getattr(getattr(trade_obj, "order", None), "totalQuantity", None))
            if remaining_qty is None:
                return 0
            return int(round(max(remaining_qty, 0.0)))

        def _emit_protection_state_locked(*, state: str, reason: str, force: bool = False) -> None:
            nonlocal protection_state
            active_tp_order_ids = _active_tp_order_ids_locked()
            if not active_tp_order_ids and not force:
                protection_state = None
                return
            if not force and protection_state == state:
                return
            protection_state = state
            if self._event_bus:
                self._event_bus.publish(
                    LadderProtectionStateChanged.now(
                        symbol=spec.symbol,
                        parent_order_id=order_id,
                        state=state,
                        reason=reason,
                        stop_order_id=_current_stop_order_id_locked(),
                        active_take_profit_order_ids=active_tp_order_ids,
                        client_tag=spec.client_tag,
                        execution_mode="detached70",
                    )
                )

        def _close_gateway_subscription_if_idle_locked() -> None:
            unsubscribe = gateway_unsubscribe_ref["fn"]
            if unsubscribe is None:
                return
            if _active_tp_order_ids_locked():
                return
            try:
                unsubscribe()
            finally:
                gateway_unsubscribe_ref["fn"] = None

        async def _handle_child_incident(
            *,
            pair_index: int,
            source_order_id: Optional[int],
            code: Optional[int],
            message: Optional[str],
        ) -> None:
            with state_lock:
                if pair_index in incident_pairs_active:
                    return
                incident_pairs_active.add(pair_index)
                other_pair_index = 2 if pair_index == 1 else 1
                child_orders = [
                    getattr(tp_trades.get(pair_index), "order", None),
                    getattr(stop_trades.get(pair_index), "order", None),
                ]
                emergency_price = min(pair_stop_price.values())

            for child_order in child_orders:
                _safe_cancel_order(self._ib, child_order)
            await asyncio.sleep(0.2)

            remaining_qty_raw = await _position_qty_for_contract(
                self._ib,
                qualified,
                account=spec.account,
                timeout=replace_timeout,
            )
            remaining_qty = int(round(max(remaining_qty_raw or 0.0, 0.0)))
            with state_lock:
                protected_qty = _active_stop_remaining_qty_locked(other_pair_index)
            uncovered_qty = int(round(max(float(remaining_qty - protected_qty), 0.0)))
            if uncovered_qty <= 0:
                with state_lock:
                    incident_pairs_active.discard(pair_index)
                    _emit_protection_state_locked(
                        state="protected",
                        reason=f"child_incident_{code if code is not None else 'unknown'}_covered",
                        force=True,
                    )
                    _close_gateway_subscription_if_idle_locked()
                return

            with state_lock:
                _emit_protection_state_locked(
                    state="unprotected",
                    reason=f"child_incident_{code if code is not None else 'unknown'}",
                    force=True,
                )

            emergency_trade = await _submit_emergency_stop(
                ib=self._ib,
                contract=qualified,
                side=child_side,
                qty=uncovered_qty,
                stop_price=emergency_price,
                tif=spec.tif,
                outside_rth=spec.outside_rth,
                account=spec.account,
                client_tag=spec.client_tag,
                use_stop_limit=use_stop_limit,
                stop_limit_buffer_pct=self._outside_rth_stop_limit_buffer_pct,
                timeout=replace_timeout,
            )
            if emergency_trade is not None:
                if self._event_bus:
                    _attach_bracket_child_handlers(
                        emergency_trade,
                        kind="det70_emergency_stop",
                        symbol=spec.symbol,
                        side=child_side,
                        qty=uncovered_qty,
                        price=emergency_price,
                        parent_order_id=order_id,
                        client_tag=spec.client_tag,
                        event_bus=self._event_bus,
                    )
                with state_lock:
                    incident_pairs_active.discard(pair_index)
                    _emit_protection_state_locked(
                        state="protected",
                        reason="emergency_stop_armed",
                        force=True,
                    )
                    _close_gateway_subscription_if_idle_locked()
                return

            if self._event_bus:
                self._event_bus.publish(
                    LadderStopLossReplaceFailed.now(
                        symbol=spec.symbol,
                        parent_order_id=order_id,
                        old_order_id=source_order_id,
                        attempted_qty=uncovered_qty,
                        attempted_price=emergency_price,
                        status="emergency_stop_failed",
                        broker_code=code,
                        broker_message=message,
                        client_tag=spec.client_tag,
                        execution_mode="detached70",
                    )
                )

            if self._connection.config.paper_only:
                await _flatten_position_market(
                    ib=self._ib,
                    contract=qualified,
                    side=child_side,
                    qty=uncovered_qty,
                    tif=spec.tif,
                    outside_rth=spec.outside_rth,
                    account=spec.account,
                    client_tag=spec.client_tag,
                )

            with state_lock:
                incident_pairs_active.discard(pair_index)
                _emit_protection_state_locked(
                    state="unprotected",
                    reason="emergency_stop_failed",
                    force=True,
                )
                _close_gateway_subscription_if_idle_locked()

        def _release_incident_inflight(pair_index: int) -> None:
            with state_lock:
                incident_pairs_inflight.discard(pair_index)

        def _schedule_child_incident(
            *,
            pair_index: int,
            source_order_id: Optional[int],
            code: Optional[int],
            message: Optional[str],
        ) -> bool:
            with state_lock:
                if pair_index in incident_pairs_inflight:
                    return False
                incident_pairs_inflight.add(pair_index)
            handle = self._schedule_managed_coroutine(
                loop,
                _handle_child_incident(
                    pair_index=pair_index,
                    source_order_id=source_order_id,
                    code=code,
                    message=message,
                ),
            )
            if handle is None:
                _release_incident_inflight(pair_index)
                return False
            handle.add_done_callback(
                lambda _done, pair_idx=pair_index: _release_incident_inflight(pair_idx)
            )
            return True

        async def _reprice_pair2_stop(*, stop_price: float) -> None:
            with state_lock:
                if 2 in incident_pairs_active or 2 in incident_pairs_inflight:
                    return
                stop_trade = stop_trades.get(2)
                if stop_trade is None:
                    return
                old_order_id = _trade_order_id(stop_trade)
                if old_order_id is None:
                    if self._event_bus:
                        self._event_bus.publish(
                            LadderStopLossReplaceFailed.now(
                                symbol=spec.symbol,
                                parent_order_id=order_id,
                                old_order_id=None,
                                attempted_qty=pair_tp_qty[2],
                                attempted_price=stop_price,
                                status="missing_stop_order_id",
                                broker_code=None,
                                broker_message="Pair-2 stop has no orderId; cannot replace.",
                                client_tag=spec.client_tag,
                                execution_mode="detached70",
                            )
                        )
                    return
                previous_price = pair_stop_price[2]
                stop_order = copy.copy(stop_trade.order)
                stop_order.orderId = old_order_id
                stop_order.parentId = 0
                stop_order.totalQuantity = pair_tp_qty[2]
                stop_order.auxPrice = stop_price
                if _is_stop_limit_order(stop_trade.order):
                    stop_order.lmtPrice = _outside_rth_stop_limit_price(
                        side=child_side,
                        stop_price=stop_price,
                        buffer_pct=self._outside_rth_stop_limit_buffer_pct,
                    )
                _apply_oca(
                    stop_order,
                    group=pair_oca_group[2],
                    oca_type=exit_oca_type,
                )
                stop_order.tif = spec.tif
                stop_order.outsideRth = spec.outside_rth
                stop_order.transmit = True
                if spec.account:
                    stop_order.account = spec.account
                if spec.client_tag:
                    stop_order.orderRef = spec.client_tag

            error_capture = _GatewayOrderErrorCapture(
                self._connection.subscribe_gateway_messages,
                order_id=old_order_id,
            )
            replace_trade = _place_order_sanitized(self._ib, qualified, stop_order)
            try:
                applied = await _wait_for_order_intent_match(
                    replace_trade,
                    expected_qty=pair_tp_qty[2],
                    expected_parent_id=0,
                    expected_oca_group=pair_oca_group[2],
                    expected_oca_type=exit_oca_type,
                    expected_aux_price=stop_price,
                    timeout=replace_timeout,
                )
            finally:
                error_capture.close()
            broker_code, broker_message = error_capture.snapshot()

            with state_lock:
                stop_trades[2] = replace_trade
                pair_stop_price[2] = stop_price if applied else previous_price
                _register_leg_locked(kind="stop", pair_index=2, trade_obj=replace_trade)
                _refresh_leg_registry_locked()

            if applied and broker_code is None:
                if self._event_bus:
                    self._event_bus.publish(
                        LadderStopLossReplaced.now(
                            symbol=spec.symbol,
                            parent_order_id=order_id,
                            old_order_id=old_order_id,
                            new_order_id=old_order_id,
                            old_qty=pair_tp_qty[2],
                            new_qty=pair_tp_qty[2],
                            old_price=previous_price,
                            new_price=stop_price,
                            reason="price_update",
                            client_tag=spec.client_tag,
                            execution_mode="detached70",
                        )
                    )
                return

            if self._event_bus:
                self._event_bus.publish(
                    LadderStopLossReplaceFailed.now(
                        symbol=spec.symbol,
                        parent_order_id=order_id,
                        old_order_id=old_order_id,
                        attempted_qty=pair_tp_qty[2],
                        attempted_price=stop_price,
                        status=_normalize_status(getattr(replace_trade.orderStatus, "status", None)),
                        broker_code=broker_code,
                        broker_message=broker_message,
                        client_tag=spec.client_tag,
                        execution_mode="detached70",
                    )
                )
            if broker_code in {201, 202, 404}:
                _schedule_child_incident(
                    pair_index=2,
                    source_order_id=old_order_id,
                    code=broker_code,
                    message=broker_message,
                )

        def _maybe_schedule_reprices_locked() -> None:
            decisions = collect_detached_reprice_decisions(
                tp_completed=tp_completed,
                milestones=reprice_milestones,
                milestone_applied=milestone_applied,
            )
            for decision in decisions:
                if 2 not in decision.target_pairs:
                    continue
                self._schedule_managed_coroutine(
                    loop,
                    _reprice_pair2_stop(stop_price=decision.stop_price),
                )

        def _gateway_incident_pair_locked(
            *,
            order_id_value: int,
            code: int,
        ) -> Optional[int]:
            return select_detached_incident_pair(
                order_id_value=order_id_value,
                code=code,
                leg_by_order_id=leg_by_order_id,
                incident_pairs_active=incident_pairs_active | incident_pairs_inflight,
                tp_completed=tp_completed,
                stop_filled=stop_filled,
                tp_has_fill=lambda idx: bool(
                    (trade_obj := tp_trades.get(idx)) is not None and _has_any_fill(trade_obj)
                ),
                stop_has_fill=lambda idx: bool(
                    (trade_obj := stop_trades.get(idx)) is not None and _has_any_fill(trade_obj)
                ),
            )

        def _on_gateway_message(
            req_id: Optional[int],
            code: Optional[int],
            message: Optional[str],
            advanced: Optional[str],
        ) -> None:
            if req_id is None or code is None:
                return
            normalized_code = int(code)
            pair_index: Optional[int] = None
            with state_lock:
                pair_index = _gateway_incident_pair_locked(
                    order_id_value=int(req_id),
                    code=normalized_code,
                )
            if pair_index is None:
                return
            _schedule_child_incident(
                pair_index=pair_index,
                source_order_id=int(req_id),
                code=normalized_code,
                message=message or advanced,
            )

        def _on_tp_status(pair_index: int, trade_obj: Trade) -> None:
            status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
            schedule_incident = False
            incident_source_order_id: Optional[int] = None
            with state_lock:
                paired_stop_trade = stop_trades.get(pair_index)
                paired_stop_filled = bool(
                    paired_stop_trade is not None and _has_any_fill(paired_stop_trade)
                )
                if status_value == "filled":
                    tp_completed[pair_index] = True
                elif (
                    status_value in _INACTIVE_ORDER_STATUSES
                    and not stop_filled[pair_index]
                    and not paired_stop_filled
                    and pair_index not in incident_pairs_active
                    and pair_index not in incident_pairs_inflight
                ):
                    schedule_incident = True
                    incident_source_order_id = _trade_order_id(trade_obj)
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()
            if schedule_incident:
                _schedule_child_incident(
                    pair_index=pair_index,
                    source_order_id=incident_source_order_id,
                    code=202,
                    message="Take-profit became inactive before paired stop resolution.",
                )

        def _on_tp_fill(pair_index: int, _trade_obj: Trade, fill_obj: object | None) -> None:
            with state_lock:
                fill_qty = _extract_execution_shares(fill_obj)
                if fill_qty is None or fill_qty <= 0:
                    return
                exec_id = _extract_execution_id(fill_obj)
                if not exec_id:
                    return
                seen_exec_ids = tp_exec_ids[pair_index]
                if exec_id in seen_exec_ids:
                    return
                seen_exec_ids.add(exec_id)
                prev_filled = tp_filled_qty[pair_index]
                capped_qty = min(float(pair_tp_qty[pair_index]), prev_filled + float(fill_qty))
                if capped_qty <= prev_filled:
                    return
                tp_filled_qty[pair_index] = capped_qty
                if capped_qty < float(pair_tp_qty[pair_index]) or tp_completed[pair_index]:
                    return
                tp_completed[pair_index] = True
                _maybe_schedule_reprices_locked()
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()

        def _on_stop_status(pair_index: int, trade_obj: Trade) -> None:
            status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
            schedule_incident = False
            incident_source_order_id: Optional[int] = None
            with state_lock:
                paired_tp_trade = tp_trades.get(pair_index)
                paired_tp_filled = bool(
                    paired_tp_trade is not None and _has_any_fill(paired_tp_trade)
                )
                if (
                    status_value in _INACTIVE_ORDER_STATUSES
                    and not stop_filled[pair_index]
                    and not tp_completed[pair_index]
                    and not paired_tp_filled
                    and pair_index not in incident_pairs_active
                    and pair_index not in incident_pairs_inflight
                ):
                    schedule_incident = True
                    incident_source_order_id = _trade_order_id(trade_obj)
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()
            if schedule_incident:
                _schedule_child_incident(
                    pair_index=pair_index,
                    source_order_id=incident_source_order_id,
                    code=202,
                    message="Protective stop became inactive before paired TP completion.",
                )

        def _on_stop_fill(pair_index: int, trade_obj: Trade) -> None:
            tp_order_to_cancel: object | None = None
            with state_lock:
                if pair_index in incident_pairs_active or pair_index in incident_pairs_inflight:
                    return
                if not _has_any_fill(trade_obj):
                    return
                stop_filled[pair_index] = True
                tp_trade = tp_trades.get(pair_index)
                if tp_trade is not None:
                    tp_order_to_cancel = getattr(tp_trade, "order", None)
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()
            if tp_order_to_cancel is not None:
                _safe_cancel_order(self._ib, tp_order_to_cancel)

        for pair_index in (1, 2):
            tp_trade = tp_trades[pair_index]
            stop_trade = stop_trades[pair_index]

            attach_trade_events(
                tp_trade,
                on_status=lambda trade_obj, *_args, pair_idx=pair_index: _on_tp_status(pair_idx, trade_obj),
                on_filled=lambda trade_obj, *args, pair_idx=pair_index: _on_tp_fill(
                    pair_idx,
                    trade_obj,
                    args[0] if args else None,
                ),
                on_fill=lambda trade_obj, *args, pair_idx=pair_index: _on_tp_fill(
                    pair_idx,
                    trade_obj,
                    args[0] if args else None,
                ),
            )
            fills = getattr(tp_trade, "fills", None)
            if fills:
                for fill_obj in list(fills):
                    _on_tp_fill(pair_index, tp_trade, fill_obj)

            attach_trade_events(
                stop_trade,
                on_status=lambda trade_obj, *_args, pair_idx=pair_index: _on_stop_status(
                    pair_idx,
                    trade_obj,
                ),
                on_filled=lambda trade_obj, *_args, pair_idx=pair_index: _on_stop_fill(
                    pair_idx,
                    trade_obj,
                ),
                on_fill=lambda trade_obj, *_args, pair_idx=pair_index: _on_stop_fill(
                    pair_idx,
                    trade_obj,
                ),
            )

        with state_lock:
            _refresh_leg_registry_locked()
            _emit_protection_state_locked(state="protected", reason="tp_registered", force=True)
            gateway_unsubscribe_ref["fn"] = self._connection.subscribe_gateway_messages(
                _on_gateway_message
            )

        final_status = _normalize_status(getattr(trade.orderStatus, "status", None)) or status
        return OrderAck.now(order_id=order_id, status=final_status)

    async def _submit_ladder_order_detached_3_pairs(
        self,
        *,
        spec: LadderOrderSpec,
        qualified: Stock,
        loop: asyncio.AbstractEventLoop,
    ) -> OrderAck:
        if len(spec.take_profits) != 3 or len(spec.take_profit_qtys) != 3:
            raise RuntimeError("Detached 3-pair mode requires exactly 3 take profits and qtys.")
        if len(spec.stop_updates) < 2:
            raise RuntimeError("Detached 3-pair mode requires 2 stop update levels.")

        trade, order_id, status = await self._submit_detached_parent_entry(
            spec=spec,
            qualified=qualified,
        )
        await self._ensure_detached_inventory_confirmed(
            spec=spec,
            trade=trade,
            qualified=qualified,
            mode_label="Detached 3-pair",
        )

        child_side = OrderSide.SELL if spec.side == OrderSide.BUY else OrderSide.BUY
        use_stop_limit = _uses_stop_limit(spec.outside_rth)
        replace_timeout = max(self._connection.config.timeout, 1.0)
        exit_oca_type = 2
        pair_indices = (1, 2, 3)
        pair_oca_group = {
            1: f"DET3A-{uuid.uuid4().hex[:10]}",
            2: f"DET3B-{uuid.uuid4().hex[:10]}",
            3: f"DET3C-{uuid.uuid4().hex[:10]}",
        }
        pair_stop_price = {idx: float(spec.stop_loss) for idx in pair_indices}
        pair_tp_price = {idx: float(spec.take_profits[idx - 1]) for idx in pair_indices}
        pair_tp_qty = {idx: int(spec.take_profit_qtys[idx - 1]) for idx in pair_indices}
        reprice_milestones = (
            DetachedRepriceMilestone(
                required_pairs=frozenset({1}),
                target_pairs=(2, 3),
                stop_price=_snap_price(float(spec.stop_updates[0]), contract=qualified),
            ),
            DetachedRepriceMilestone(
                required_pairs=frozenset({1, 2}),
                target_pairs=(3,),
                stop_price=_snap_price(float(spec.stop_updates[1]), contract=qualified),
            ),
        )

        stop_trades: dict[int, Trade] = {}
        tp_trades: dict[int, Trade] = {}

        for pair_index in pair_indices:
            qty = pair_tp_qty[pair_index]
            stop_price = pair_stop_price[pair_index]
            oca_group = pair_oca_group[pair_index]

            def _stop_factory(
                *,
                pair_qty: int = qty,
                pair_stop_price: float = stop_price,
                group: str = oca_group,
            ) -> object:
                stop_order = _build_protective_stop_order(
                    side=child_side,
                    qty=pair_qty,
                    stop_price=pair_stop_price,
                    limit_price=pair_stop_price,
                    tif=spec.tif,
                    outside_rth=spec.outside_rth,
                    account=spec.account,
                    client_tag=spec.client_tag,
                    use_stop_limit=use_stop_limit,
                    stop_limit_buffer_pct=self._outside_rth_stop_limit_buffer_pct,
                )
                _apply_oca(stop_order, group=group, oca_type=exit_oca_type)
                stop_order.parentId = 0
                stop_order.transmit = True
                return stop_order

            stop_trade = await _place_order_with_reconcile(
                ib=self._ib,
                contract=qualified,
                order_factory=_stop_factory,
                expected_qty=qty,
                expected_parent_id=0,
                expected_oca_group=oca_group,
                expected_oca_type=exit_oca_type,
                timeout=replace_timeout,
            )
            stop_trades[pair_index] = stop_trade
            _publish_stop_mode_selected(
                event_bus=self._event_bus,
                symbol=spec.symbol,
                parent_order_id=order_id,
                trade=stop_trade,
                session_phase=SessionPhase.RTH if not spec.outside_rth else SessionPhase.OUTSIDE_RTH,
                stop_price=stop_price,
                execution_mode="detached",
                client_tag=spec.client_tag,
            )
            if self._event_bus:
                _attach_bracket_child_handlers(
                    stop_trade,
                    kind=f"detached_stop_{pair_index}",
                    symbol=spec.symbol,
                    side=child_side,
                    qty=qty,
                    price=stop_price,
                    parent_order_id=order_id,
                    client_tag=spec.client_tag,
                    event_bus=self._event_bus,
                )

            tp_price = pair_tp_price[pair_index]

            def _tp_factory(
                *,
                pair_qty: int = qty,
                pair_tp_price: float = tp_price,
                group: str = oca_group,
            ) -> object:
                tp_order = LimitOrder(child_side.value, pair_qty, pair_tp_price, tif=spec.tif)
                _apply_oca(tp_order, group=group, oca_type=exit_oca_type)
                tp_order.parentId = 0
                tp_order.transmit = True
                tp_order.outsideRth = spec.outside_rth
                if spec.account:
                    tp_order.account = spec.account
                if spec.client_tag:
                    tp_order.orderRef = spec.client_tag
                return tp_order

            tp_trade = await _place_order_with_reconcile(
                ib=self._ib,
                contract=qualified,
                order_factory=_tp_factory,
                expected_qty=qty,
                expected_parent_id=0,
                expected_oca_group=oca_group,
                expected_oca_type=exit_oca_type,
                timeout=replace_timeout,
            )
            tp_trades[pair_index] = tp_trade
            if self._event_bus:
                _attach_bracket_child_handlers(
                    tp_trade,
                    kind=f"detached_tp_{pair_index}",
                    symbol=spec.symbol,
                    side=child_side,
                    qty=qty,
                    price=tp_price,
                    parent_order_id=order_id,
                    client_tag=spec.client_tag,
                    event_bus=self._event_bus,
                )

        state_lock = threading.Lock()
        tp_exec_ids: dict[int, set[str]] = {idx: set() for idx in pair_indices}
        tp_filled_qty: dict[int, float] = {idx: 0.0 for idx in pair_indices}
        tp_completed: dict[int, bool] = {idx: False for idx in pair_indices}
        stop_filled: dict[int, bool] = {idx: False for idx in pair_indices}
        protection_state: Optional[str] = None
        incident_pairs_active: set[int] = set()
        incident_pairs_inflight: set[int] = set()
        leg_by_order_id: dict[int, tuple[str, int]] = {}
        gateway_unsubscribe_ref: dict[str, Optional[Callable[[], None]]] = {"fn": None}
        milestone_applied = [False] * len(reprice_milestones)

        def _register_leg_locked(*, kind: str, pair_index: int, trade_obj: Optional[Trade]) -> None:
            order_id_value = _trade_order_id(trade_obj)
            if order_id_value is None:
                return
            leg_by_order_id[order_id_value] = (kind, pair_index)

        def _refresh_leg_registry_locked() -> None:
            leg_by_order_id.clear()
            for idx in pair_indices:
                _register_leg_locked(kind="stop", pair_index=idx, trade_obj=stop_trades.get(idx))
            for idx in pair_indices:
                _register_leg_locked(kind="tp", pair_index=idx, trade_obj=tp_trades.get(idx))

        def _active_tp_order_ids_locked() -> list[int]:
            ids: list[int] = []
            for idx in pair_indices:
                trade_obj = tp_trades.get(idx)
                order_id_value = _trade_order_id(trade_obj)
                if order_id_value is None:
                    continue
                status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
                if status_value in _INACTIVE_ORDER_STATUSES and not tp_completed[idx]:
                    continue
                if tp_completed[idx] and status_value in _INACTIVE_ORDER_STATUSES:
                    continue
                ids.append(order_id_value)
            ids.sort()
            return ids

        def _current_stop_order_id_locked() -> Optional[int]:
            for idx in reversed(pair_indices):
                trade_obj = stop_trades.get(idx)
                if trade_obj is None:
                    continue
                status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
                if status_value in _INACTIVE_ORDER_STATUSES:
                    continue
                order_id_value = _trade_order_id(trade_obj)
                if order_id_value is not None:
                    return order_id_value
            for idx in reversed(pair_indices):
                order_id_value = _trade_order_id(stop_trades.get(idx))
                if order_id_value is not None:
                    return order_id_value
            return None

        def _active_stop_remaining_qty_locked(pair_index: int) -> int:
            trade_obj = stop_trades.get(pair_index)
            if trade_obj is None:
                return 0
            status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
            if status_value in _INACTIVE_ORDER_STATUSES:
                return 0
            remaining_qty = _maybe_float(getattr(trade_obj.orderStatus, "remaining", None))
            if remaining_qty is None:
                remaining_qty = _maybe_float(
                    getattr(getattr(trade_obj, "order", None), "totalQuantity", None)
                )
            if remaining_qty is None:
                return 0
            return int(round(max(remaining_qty, 0.0)))

        def _active_stop_total_remaining_qty_locked(*, exclude_pair: Optional[int] = None) -> int:
            total = 0
            for idx in pair_indices:
                if exclude_pair is not None and idx == exclude_pair:
                    continue
                total += _active_stop_remaining_qty_locked(idx)
            return total

        def _emit_protection_state_locked(*, state: str, reason: str, force: bool = False) -> None:
            nonlocal protection_state
            active_tp_order_ids = _active_tp_order_ids_locked()
            if not active_tp_order_ids and not force:
                protection_state = None
                return
            if not force and protection_state == state:
                return
            protection_state = state
            if self._event_bus:
                self._event_bus.publish(
                    LadderProtectionStateChanged.now(
                        symbol=spec.symbol,
                        parent_order_id=order_id,
                        state=state,
                        reason=reason,
                        stop_order_id=_current_stop_order_id_locked(),
                        active_take_profit_order_ids=active_tp_order_ids,
                        client_tag=spec.client_tag,
                        execution_mode="detached",
                    )
                )

        def _close_gateway_subscription_if_idle_locked() -> None:
            unsubscribe = gateway_unsubscribe_ref["fn"]
            if unsubscribe is None:
                return
            if _active_tp_order_ids_locked():
                return
            try:
                unsubscribe()
            finally:
                gateway_unsubscribe_ref["fn"] = None

        async def _handle_child_incident(
            *,
            pair_index: int,
            source_order_id: Optional[int],
            code: Optional[int],
            message: Optional[str],
        ) -> None:
            with state_lock:
                if pair_index in incident_pairs_active:
                    return
                incident_pairs_active.add(pair_index)
                child_orders = [
                    getattr(tp_trades.get(pair_index), "order", None),
                    getattr(stop_trades.get(pair_index), "order", None),
                ]
                emergency_price = min(pair_stop_price.values())

            for child_order in child_orders:
                _safe_cancel_order(self._ib, child_order)
            await asyncio.sleep(0.2)

            remaining_qty_raw = await _position_qty_for_contract(
                self._ib,
                qualified,
                account=spec.account,
                timeout=replace_timeout,
            )
            remaining_qty = int(round(max(remaining_qty_raw or 0.0, 0.0)))
            with state_lock:
                protected_qty = _active_stop_total_remaining_qty_locked(exclude_pair=pair_index)
            uncovered_qty = int(round(max(float(remaining_qty - protected_qty), 0.0)))
            if uncovered_qty <= 0:
                with state_lock:
                    incident_pairs_active.discard(pair_index)
                    _emit_protection_state_locked(
                        state="protected",
                        reason=f"child_incident_{code if code is not None else 'unknown'}_covered",
                        force=True,
                    )
                    _close_gateway_subscription_if_idle_locked()
                return

            with state_lock:
                _emit_protection_state_locked(
                    state="unprotected",
                    reason=f"child_incident_{code if code is not None else 'unknown'}",
                    force=True,
                )

            emergency_trade = await _submit_emergency_stop(
                ib=self._ib,
                contract=qualified,
                side=child_side,
                qty=uncovered_qty,
                stop_price=emergency_price,
                tif=spec.tif,
                outside_rth=spec.outside_rth,
                account=spec.account,
                client_tag=spec.client_tag,
                use_stop_limit=use_stop_limit,
                stop_limit_buffer_pct=self._outside_rth_stop_limit_buffer_pct,
                timeout=replace_timeout,
            )
            if emergency_trade is not None:
                if self._event_bus:
                    _attach_bracket_child_handlers(
                        emergency_trade,
                        kind="det70_emergency_stop",
                        symbol=spec.symbol,
                        side=child_side,
                        qty=uncovered_qty,
                        price=emergency_price,
                        parent_order_id=order_id,
                        client_tag=spec.client_tag,
                        event_bus=self._event_bus,
                    )
                with state_lock:
                    incident_pairs_active.discard(pair_index)
                    _emit_protection_state_locked(
                        state="protected",
                        reason="emergency_stop_armed",
                        force=True,
                    )
                    _close_gateway_subscription_if_idle_locked()
                return

            if self._event_bus:
                self._event_bus.publish(
                    LadderStopLossReplaceFailed.now(
                        symbol=spec.symbol,
                        parent_order_id=order_id,
                        old_order_id=source_order_id,
                        attempted_qty=uncovered_qty,
                        attempted_price=emergency_price,
                        status="emergency_stop_failed",
                        broker_code=code,
                        broker_message=message,
                        client_tag=spec.client_tag,
                        execution_mode="detached",
                    )
                )

            if self._connection.config.paper_only:
                await _flatten_position_market(
                    ib=self._ib,
                    contract=qualified,
                    side=child_side,
                    qty=uncovered_qty,
                    tif=spec.tif,
                    outside_rth=spec.outside_rth,
                    account=spec.account,
                    client_tag=spec.client_tag,
                )

            with state_lock:
                incident_pairs_active.discard(pair_index)
                _emit_protection_state_locked(
                    state="unprotected",
                    reason="emergency_stop_failed",
                    force=True,
                )
                _close_gateway_subscription_if_idle_locked()

        def _release_incident_inflight(pair_index: int) -> None:
            with state_lock:
                incident_pairs_inflight.discard(pair_index)

        def _schedule_child_incident(
            *,
            pair_index: int,
            source_order_id: Optional[int],
            code: Optional[int],
            message: Optional[str],
        ) -> bool:
            with state_lock:
                if pair_index in incident_pairs_inflight:
                    return False
                incident_pairs_inflight.add(pair_index)
            handle = self._schedule_managed_coroutine(
                loop,
                _handle_child_incident(
                    pair_index=pair_index,
                    source_order_id=source_order_id,
                    code=code,
                    message=message,
                ),
            )
            if handle is None:
                _release_incident_inflight(pair_index)
                return False
            handle.add_done_callback(
                lambda _done, pair_idx=pair_index: _release_incident_inflight(pair_idx)
            )
            return True

        async def _reprice_single_pair_stop(
            *,
            pair_index: int,
            stop_price: float,
        ) -> None:
            with state_lock:
                if pair_index in incident_pairs_active or pair_index in incident_pairs_inflight:
                    return
                stop_trade = stop_trades.get(pair_index)
                if stop_trade is None:
                    return
                if tp_completed[pair_index] or stop_filled[pair_index]:
                    return
                current_status = _normalize_status(getattr(stop_trade.orderStatus, "status", None))
                if current_status in _INACTIVE_ORDER_STATUSES:
                    return
                old_order_id = _trade_order_id(stop_trade)
                if old_order_id is None:
                    if self._event_bus:
                        self._event_bus.publish(
                            LadderStopLossReplaceFailed.now(
                                symbol=spec.symbol,
                                parent_order_id=order_id,
                                old_order_id=None,
                                attempted_qty=pair_tp_qty[pair_index],
                                attempted_price=stop_price,
                                status="missing_stop_order_id",
                                broker_code=None,
                                broker_message=f"Pair-{pair_index} stop has no orderId; cannot replace.",
                                client_tag=spec.client_tag,
                                execution_mode="detached",
                            )
                        )
                    return
                previous_price = pair_stop_price[pair_index]
                stop_order = copy.copy(stop_trade.order)
                stop_order.orderId = old_order_id
                stop_order.parentId = 0
                stop_order.totalQuantity = pair_tp_qty[pair_index]
                stop_order.auxPrice = stop_price
                if _is_stop_limit_order(stop_trade.order):
                    stop_order.lmtPrice = _outside_rth_stop_limit_price(
                        side=child_side,
                        stop_price=stop_price,
                        buffer_pct=self._outside_rth_stop_limit_buffer_pct,
                    )
                _apply_oca(
                    stop_order,
                    group=pair_oca_group[pair_index],
                    oca_type=exit_oca_type,
                )
                stop_order.tif = spec.tif
                stop_order.outsideRth = spec.outside_rth
                stop_order.transmit = True
                if spec.account:
                    stop_order.account = spec.account
                if spec.client_tag:
                    stop_order.orderRef = spec.client_tag

            error_capture = _GatewayOrderErrorCapture(
                self._connection.subscribe_gateway_messages,
                order_id=old_order_id,
            )
            replace_trade = _place_order_sanitized(self._ib, qualified, stop_order)
            try:
                applied = await _wait_for_order_intent_match(
                    replace_trade,
                    expected_qty=pair_tp_qty[pair_index],
                    expected_parent_id=0,
                    expected_oca_group=pair_oca_group[pair_index],
                    expected_oca_type=exit_oca_type,
                    expected_aux_price=stop_price,
                    timeout=replace_timeout,
                )
            finally:
                error_capture.close()
            broker_code, broker_message = error_capture.snapshot()

            with state_lock:
                stop_trades[pair_index] = replace_trade
                pair_stop_price[pair_index] = stop_price if applied else previous_price
                _register_leg_locked(kind="stop", pair_index=pair_index, trade_obj=replace_trade)
                _refresh_leg_registry_locked()

            if applied and broker_code is None:
                if self._event_bus:
                    self._event_bus.publish(
                        LadderStopLossReplaced.now(
                            symbol=spec.symbol,
                            parent_order_id=order_id,
                            old_order_id=old_order_id,
                            new_order_id=old_order_id,
                            old_qty=pair_tp_qty[pair_index],
                            new_qty=pair_tp_qty[pair_index],
                            old_price=previous_price,
                            new_price=stop_price,
                            reason="price_update",
                            client_tag=spec.client_tag,
                            execution_mode="detached",
                        )
                    )
                return

            if self._event_bus:
                self._event_bus.publish(
                    LadderStopLossReplaceFailed.now(
                        symbol=spec.symbol,
                        parent_order_id=order_id,
                        old_order_id=old_order_id,
                        attempted_qty=pair_tp_qty[pair_index],
                        attempted_price=stop_price,
                        status=_normalize_status(getattr(replace_trade.orderStatus, "status", None)),
                        broker_code=broker_code,
                        broker_message=broker_message,
                        client_tag=spec.client_tag,
                        execution_mode="detached",
                    )
                )
            if broker_code in {201, 202, 404}:
                _schedule_child_incident(
                    pair_index=pair_index,
                    source_order_id=old_order_id,
                    code=broker_code,
                    message=broker_message,
                )

        async def _reprice_pairs(*, target_pairs: tuple[int, ...], stop_price: float) -> None:
            for pair_index in target_pairs:
                await _reprice_single_pair_stop(pair_index=pair_index, stop_price=stop_price)

        def _maybe_schedule_reprices_locked() -> None:
            decisions = collect_detached_reprice_decisions(
                tp_completed=tp_completed,
                milestones=reprice_milestones,
                milestone_applied=milestone_applied,
            )
            for decision in decisions:
                self._schedule_managed_coroutine(
                    loop,
                    _reprice_pairs(target_pairs=decision.target_pairs, stop_price=decision.stop_price),
                )

        def _gateway_incident_pair_locked(
            *,
            order_id_value: int,
            code: int,
        ) -> Optional[int]:
            return select_detached_incident_pair(
                order_id_value=order_id_value,
                code=code,
                leg_by_order_id=leg_by_order_id,
                incident_pairs_active=incident_pairs_active | incident_pairs_inflight,
                tp_completed=tp_completed,
                stop_filled=stop_filled,
                tp_has_fill=lambda idx: bool(
                    (trade_obj := tp_trades.get(idx)) is not None and _has_any_fill(trade_obj)
                ),
                stop_has_fill=lambda idx: bool(
                    (trade_obj := stop_trades.get(idx)) is not None and _has_any_fill(trade_obj)
                ),
            )

        def _on_gateway_message(
            req_id: Optional[int],
            code: Optional[int],
            message: Optional[str],
            advanced: Optional[str],
        ) -> None:
            if req_id is None or code is None:
                return
            normalized_code = int(code)
            pair_index: Optional[int]
            with state_lock:
                pair_index = _gateway_incident_pair_locked(
                    order_id_value=int(req_id),
                    code=normalized_code,
                )
            if pair_index is None:
                return
            _schedule_child_incident(
                pair_index=pair_index,
                source_order_id=int(req_id),
                code=normalized_code,
                message=message or advanced,
            )

        def _on_tp_status(pair_index: int, trade_obj: Trade) -> None:
            status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
            schedule_incident = False
            incident_source_order_id: Optional[int] = None
            with state_lock:
                paired_stop_trade = stop_trades.get(pair_index)
                paired_stop_filled = bool(
                    paired_stop_trade is not None and _has_any_fill(paired_stop_trade)
                )
                if status_value == "filled":
                    tp_completed[pair_index] = True
                elif (
                    status_value in _INACTIVE_ORDER_STATUSES
                    and not stop_filled[pair_index]
                    and not paired_stop_filled
                    and pair_index not in incident_pairs_active
                    and pair_index not in incident_pairs_inflight
                ):
                    schedule_incident = True
                    incident_source_order_id = _trade_order_id(trade_obj)
                _maybe_schedule_reprices_locked()
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()
            if schedule_incident:
                _schedule_child_incident(
                    pair_index=pair_index,
                    source_order_id=incident_source_order_id,
                    code=202,
                    message="Take-profit became inactive before paired stop resolution.",
                )

        def _on_tp_fill(pair_index: int, _trade_obj: Trade, fill_obj: object | None) -> None:
            with state_lock:
                fill_qty = _extract_execution_shares(fill_obj)
                if fill_qty is None or fill_qty <= 0:
                    return
                exec_id = _extract_execution_id(fill_obj)
                if not exec_id:
                    return
                seen_exec_ids = tp_exec_ids[pair_index]
                if exec_id in seen_exec_ids:
                    return
                seen_exec_ids.add(exec_id)
                prev_filled = tp_filled_qty[pair_index]
                capped_qty = min(float(pair_tp_qty[pair_index]), prev_filled + float(fill_qty))
                if capped_qty <= prev_filled:
                    return
                tp_filled_qty[pair_index] = capped_qty
                if capped_qty < float(pair_tp_qty[pair_index]) or tp_completed[pair_index]:
                    return
                tp_completed[pair_index] = True
                _maybe_schedule_reprices_locked()
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()

        def _on_stop_status(pair_index: int, trade_obj: Trade) -> None:
            status_value = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
            schedule_incident = False
            incident_source_order_id: Optional[int] = None
            with state_lock:
                paired_tp_trade = tp_trades.get(pair_index)
                paired_tp_filled = bool(
                    paired_tp_trade is not None and _has_any_fill(paired_tp_trade)
                )
                if (
                    status_value in _INACTIVE_ORDER_STATUSES
                    and not stop_filled[pair_index]
                    and not tp_completed[pair_index]
                    and not paired_tp_filled
                    and pair_index not in incident_pairs_active
                    and pair_index not in incident_pairs_inflight
                ):
                    schedule_incident = True
                    incident_source_order_id = _trade_order_id(trade_obj)
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()
            if schedule_incident:
                _schedule_child_incident(
                    pair_index=pair_index,
                    source_order_id=incident_source_order_id,
                    code=202,
                    message="Protective stop became inactive before paired TP completion.",
                )

        def _on_stop_fill(pair_index: int, trade_obj: Trade) -> None:
            tp_order_to_cancel: object | None = None
            with state_lock:
                if pair_index in incident_pairs_active or pair_index in incident_pairs_inflight:
                    return
                if not _has_any_fill(trade_obj):
                    return
                stop_filled[pair_index] = True
                tp_trade = tp_trades.get(pair_index)
                if tp_trade is not None:
                    tp_order_to_cancel = getattr(tp_trade, "order", None)
                _refresh_leg_registry_locked()
                _close_gateway_subscription_if_idle_locked()
            if tp_order_to_cancel is not None:
                _safe_cancel_order(self._ib, tp_order_to_cancel)

        for pair_index in pair_indices:
            tp_trade = tp_trades[pair_index]
            stop_trade = stop_trades[pair_index]

            attach_trade_events(
                tp_trade,
                on_status=lambda trade_obj, *_args, pair_idx=pair_index: _on_tp_status(pair_idx, trade_obj),
                on_filled=lambda trade_obj, *args, pair_idx=pair_index: _on_tp_fill(
                    pair_idx,
                    trade_obj,
                    args[0] if args else None,
                ),
                on_fill=lambda trade_obj, *args, pair_idx=pair_index: _on_tp_fill(
                    pair_idx,
                    trade_obj,
                    args[0] if args else None,
                ),
            )
            fills = getattr(tp_trade, "fills", None)
            if fills:
                for fill_obj in list(fills):
                    _on_tp_fill(pair_index, tp_trade, fill_obj)

            attach_trade_events(
                stop_trade,
                on_status=lambda trade_obj, *_args, pair_idx=pair_index: _on_stop_status(
                    pair_idx,
                    trade_obj,
                ),
                on_filled=lambda trade_obj, *_args, pair_idx=pair_index: _on_stop_fill(
                    pair_idx,
                    trade_obj,
                ),
                on_fill=lambda trade_obj, *_args, pair_idx=pair_index: _on_stop_fill(
                    pair_idx,
                    trade_obj,
                ),
            )

        with state_lock:
            _refresh_leg_registry_locked()
            _emit_protection_state_locked(state="protected", reason="tp_registered", force=True)
            gateway_unsubscribe_ref["fn"] = self._connection.subscribe_gateway_messages(
                _on_gateway_message
            )

        final_status = _normalize_status(getattr(trade.orderStatus, "status", None)) or status
        return OrderAck.now(order_id=order_id, status=final_status)


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


async def _wait_for_inventory_confirmation(
    *,
    ib: IB,
    trade: Trade,
    contract: Stock,
    account: Optional[str],
    expected_qty: int,
    timeout: float,
    poll_interval: float = 0.05,
    position_poll_interval: float = 0.25,
) -> tuple[bool, float, Optional[float]]:
    deadline = time.time() + max(timeout, 0.5)
    last_position_qty: Optional[float] = None
    next_position_poll = 0.0
    last_filled_qty = 0.0
    while time.time() < deadline:
        status = _normalize_status(getattr(trade.orderStatus, "status", None))
        order_filled_qty = _maybe_float(getattr(trade.orderStatus, "filled", None)) or 0.0
        execution_filled_qty = _trade_execution_qty(trade)
        last_filled_qty = max(order_filled_qty, execution_filled_qty)
        if status == "filled" and last_filled_qty >= float(expected_qty):
            now = time.time()
            if now >= next_position_poll:
                last_position_qty = await _position_qty_for_contract(
                    ib,
                    contract,
                    account=account,
                    timeout=min(max(timeout, 0.5), 1.0),
                )
                next_position_poll = now + max(position_poll_interval, 0.1)
            if last_position_qty is not None and last_position_qty >= float(expected_qty):
                return True, last_filled_qty, last_position_qty
        await asyncio.sleep(max(poll_interval, 0.01))
    return False, last_filled_qty, last_position_qty


def _trade_execution_qty(trade: Trade) -> float:
    fills = getattr(trade, "fills", None)
    if not fills:
        return 0.0
    total = 0.0
    for fill_obj in list(fills):
        shares = _extract_execution_shares(fill_obj)
        if shares is None or shares <= 0:
            continue
        total += float(shares)
    return total


async def _position_qty_for_contract(
    ib: IB,
    contract: Stock,
    *,
    account: Optional[str],
    timeout: float,
) -> Optional[float]:
    try:
        positions = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=max(timeout, 0.25))
    except Exception:
        return None

    total_qty = 0.0
    matched_any = False
    for item in positions:
        item_account = _normalize_account(getattr(item, "account", None))
        if account is not None and item_account != _normalize_account(account):
            continue
        item_contract = getattr(item, "contract", None)
        if item_contract is None or not _contracts_match(contract, item_contract):
            continue
        qty = _maybe_float(getattr(item, "position", None))
        if qty is None:
            continue
        total_qty += qty
        matched_any = True
    if matched_any:
        return total_qty
    return 0.0


def _normalize_account(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized.rstrip(".")


def _contracts_match(expected: object, actual: object) -> bool:
    expected_con_id = _maybe_int(getattr(expected, "conId", None))
    actual_con_id = _maybe_int(getattr(actual, "conId", None))
    if expected_con_id is not None and actual_con_id is not None:
        return expected_con_id == actual_con_id
    expected_symbol = str(getattr(expected, "symbol", "") or "").strip().upper()
    actual_symbol = str(getattr(actual, "symbol", "") or "").strip().upper()
    if not expected_symbol or expected_symbol != actual_symbol:
        return False
    expected_currency = str(getattr(expected, "currency", "") or "").strip().upper()
    actual_currency = str(getattr(actual, "currency", "") or "").strip().upper()
    if expected_currency and actual_currency and expected_currency != actual_currency:
        return False
    return True


async def _place_order_with_reconcile(
    *,
    ib: IB,
    contract: Stock,
    order_factory: Callable[[], object],
    expected_qty: int,
    expected_parent_id: Optional[int],
    expected_oca_group: Optional[str],
    expected_oca_type: Optional[int],
    expected_aux_price: Optional[float] = None,
    timeout: float,
    retries: int = 1,
) -> Trade:
    trade = _place_order_sanitized(ib, contract, order_factory())
    for _attempt in range(retries + 1):
        matched = await _wait_for_order_intent_match(
            trade,
            expected_qty=expected_qty,
            expected_parent_id=expected_parent_id,
            expected_oca_group=expected_oca_group,
            expected_oca_type=expected_oca_type,
            expected_aux_price=expected_aux_price,
            timeout=timeout,
        )
        if matched:
            return trade
        if _attempt >= retries:
            return trade
        status = _normalize_status(getattr(getattr(trade, "orderStatus", None), "status", None))
        if _has_any_fill(trade) or status == "filled":
            return trade
        _safe_cancel_order(ib, getattr(trade, "order", None))
        await asyncio.sleep(0.2)
        trade = _place_order_sanitized(ib, contract, order_factory())
    return trade


async def _wait_for_order_intent_match(
    trade: Trade,
    *,
    expected_qty: int,
    expected_parent_id: Optional[int],
    expected_oca_group: Optional[str],
    expected_oca_type: Optional[int],
    expected_aux_price: Optional[float] = None,
    timeout: float,
    poll_interval: float = 0.05,
) -> bool:
    deadline = time.time() + max(timeout, 0.5)
    while time.time() < deadline:
        order_obj = getattr(trade, "order", None)
        status = _normalize_status(getattr(getattr(trade, "orderStatus", None), "status", None))
        if _order_matches_intent(
            order_obj,
            expected_qty=expected_qty,
            expected_parent_id=expected_parent_id,
            expected_oca_group=expected_oca_group,
            expected_oca_type=expected_oca_type,
            expected_aux_price=expected_aux_price,
        ):
            return True
        if status in _INACTIVE_ORDER_STATUSES:
            return False
        await asyncio.sleep(max(poll_interval, 0.01))
    return _order_matches_intent(
        getattr(trade, "order", None),
        expected_qty=expected_qty,
        expected_parent_id=expected_parent_id,
        expected_oca_group=expected_oca_group,
        expected_oca_type=expected_oca_type,
        expected_aux_price=expected_aux_price,
    )


def _order_matches_intent(
    order_obj: object,
    *,
    expected_qty: int,
    expected_parent_id: Optional[int],
    expected_oca_group: Optional[str],
    expected_oca_type: Optional[int],
    expected_aux_price: Optional[float] = None,
) -> bool:
    if order_obj is None:
        return False
    broker_qty = _maybe_float(getattr(order_obj, "totalQuantity", None))
    if broker_qty is None or int(round(broker_qty)) != int(expected_qty):
        return False
    if expected_parent_id is not None:
        broker_parent_id = _maybe_int(getattr(order_obj, "parentId", None)) or 0
        if broker_parent_id != int(expected_parent_id):
            return False
    if expected_oca_group is not None:
        broker_oca_group = str(getattr(order_obj, "ocaGroup", "") or "")
        if broker_oca_group != str(expected_oca_group):
            return False
    if expected_oca_type is not None:
        broker_oca_type = _maybe_int(getattr(order_obj, "ocaType", None))
        if broker_oca_type != int(expected_oca_type):
            return False
    if expected_aux_price is not None:
        broker_aux_price = _maybe_float(getattr(order_obj, "auxPrice", None))
        if broker_aux_price is None or abs(broker_aux_price - expected_aux_price) > 1e-9:
            return False
    return True


def _safe_cancel_order(ib: IB, order_obj: object | None) -> None:
    if order_obj is None:
        return
    try:
        ib.cancelOrder(order_obj)
    except Exception:
        return


async def _flatten_position_market(
    *,
    ib: IB,
    contract: Stock,
    side: OrderSide,
    qty: int,
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    client_tag: Optional[str],
) -> Optional[Trade]:
    if qty <= 0:
        return None
    flatten_order = MarketOrder(side.value, qty, tif=tif)
    flatten_order.outsideRth = outside_rth
    if account:
        flatten_order.account = account
    if client_tag:
        flatten_order.orderRef = client_tag
    trade = _place_order_sanitized(ib, contract, flatten_order)
    await _wait_for_order_status(trade, timeout=2.0)
    return trade


async def _submit_emergency_stop(
    *,
    ib: IB,
    contract: Stock,
    side: OrderSide,
    qty: int,
    stop_price: float,
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    client_tag: Optional[str],
    use_stop_limit: bool,
    stop_limit_buffer_pct: float,
    timeout: float,
) -> Optional[Trade]:
    if qty <= 0:
        return None
    emergency_stop = _build_protective_stop_order(
        side=side,
        qty=qty,
        stop_price=stop_price,
        limit_price=stop_price,
        tif=tif,
        outside_rth=outside_rth,
        account=account,
        client_tag=client_tag,
        use_stop_limit=use_stop_limit,
        stop_limit_buffer_pct=stop_limit_buffer_pct,
    )
    emergency_stop.parentId = 0
    emergency_stop.transmit = True
    trade = _place_order_sanitized(ib, contract, emergency_stop)
    status = await _wait_for_order_status(trade, timeout=max(timeout, 1.0))
    normalized_status = _normalize_status(status)
    if normalized_status in _INACTIVE_ORDER_STATUSES:
        return None
    return trade


def _find_trade_by_order_id(ib: IB, order_id: int) -> Optional[Trade]:
    for trade in ib.trades():
        if getattr(getattr(trade, "order", None), "orderId", None) == order_id:
            return trade
    return None


async def _find_trade_by_order_id_with_refresh(
    ib: IB,
    order_id: int,
    *,
    timeout: float,
) -> Optional[Trade]:
    trade = _find_trade_by_order_id(ib, order_id)
    if trade is not None:
        return trade
    try:
        await asyncio.wait_for(ib.reqOpenOrdersAsync(), timeout=timeout)
    except Exception:
        pass
    trade = _find_trade_by_order_id(ib, order_id)
    if trade is not None:
        return trade
    try:
        await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=timeout)
    except Exception:
        pass
    return _find_trade_by_order_id(ib, order_id)


def _trade_order_id(trade: Optional[Trade]) -> Optional[int]:
    if trade is None:
        return None
    return _maybe_int(getattr(getattr(trade, "order", None), "orderId", None))


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


class _GatewayOrderErrorCapture:
    def __init__(
        self,
        subscribe: Optional[
            Callable[
                [Callable[[Optional[int], Optional[int], Optional[str], Optional[str]], None]],
                Callable[[], None],
            ]
        ],
        *,
        order_id: Optional[int],
    ) -> None:
        self._order_id = order_id
        self._code: Optional[int] = None
        self._message: Optional[str] = None
        self._lock = threading.Lock()
        self._unsubscribe: Optional[Callable[[], None]] = None
        if subscribe is not None and order_id is not None:
            self._unsubscribe = subscribe(self._handle)

    def _handle(
        self,
        req_id: Optional[int],
        code: Optional[int],
        message: Optional[str],
        advanced: Optional[str],
    ) -> None:
        if self._order_id is None:
            return
        if req_id != self._order_id:
            return
        with self._lock:
            if code is not None:
                self._code = int(code)
            if message:
                self._message = message
            elif advanced:
                self._message = advanced

    def snapshot(self) -> tuple[Optional[int], Optional[str]]:
        with self._lock:
            return self._code, self._message

    def close(self) -> None:
        if not self._unsubscribe:
            return
        try:
            self._unsubscribe()
        finally:
            self._unsubscribe = None


def _place_order_sanitized(ib: IB, contract: object, order: object) -> Trade:
    _sanitize_order_prices(order, contract=contract)
    return ib.placeOrder(contract, order)


def _sanitize_order_prices(order: object, *, contract: object) -> None:
    lmt_price = _maybe_float(getattr(order, "lmtPrice", None))
    if (
        lmt_price is not None
        and math.isfinite(lmt_price)
        and lmt_price > 0
        and not _is_ib_unset_double(lmt_price)
    ):
        setattr(order, "lmtPrice", _snap_price(lmt_price, contract=contract))

    aux_price = _maybe_float(getattr(order, "auxPrice", None))
    if (
        aux_price is not None
        and math.isfinite(aux_price)
        and aux_price > 0
        and not _is_ib_unset_double(aux_price)
    ):
        setattr(order, "auxPrice", _snap_price(aux_price, contract=contract))

    if _is_stop_limit_order(order):
        _sanitize_stop_limit_prices(order)


def _sanitize_stop_limit_prices(order: object) -> None:
    lmt_price = _maybe_float(getattr(order, "lmtPrice", None))
    aux_price = _maybe_float(getattr(order, "auxPrice", None))
    if lmt_price is None or aux_price is None:
        return
    action = str(getattr(order, "action", "")).strip().upper()
    if action == "SELL" and lmt_price > aux_price:
        setattr(order, "lmtPrice", aux_price)
        return
    if action == "BUY" and lmt_price < aux_price:
        setattr(order, "lmtPrice", aux_price)


def _snap_price(price: float, *, contract: object) -> float:
    tick_size = _tick_size_for_contract(contract, price=price)
    return _round_to_tick(price, tick=tick_size)


def _tick_size_for_contract(contract: object, *, price: float) -> float:
    contract_tick = _maybe_float(getattr(contract, "minTick", None))
    if (
        contract_tick is not None
        and math.isfinite(contract_tick)
        and contract_tick > 0
        and not _is_ib_unset_double(contract_tick)
    ):
        return contract_tick
    # TODO: use IB market-rule price increments when available; fallback keeps current behavior predictable.
    return _tick_size_for_price(price)


def _tick_size_for_price(price: float) -> float:
    if not math.isfinite(price) or price <= 0:
        return 0.01
    if price < 1.0:
        return 0.0001
    return 0.01


def _round_to_tick(price: float, *, tick: float) -> float:
    if not math.isfinite(price) or not math.isfinite(tick) or tick <= 0:
        return price
    try:
        tick_dec = Decimal(str(tick))
        price_dec = Decimal(str(price))
        steps = (price_dec / tick_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        rounded = steps * tick_dec
        return float(rounded)
    except (InvalidOperation, OverflowError, ValueError):
        return price


def _apply_oca(order_obj: object, *, group: str, oca_type: int) -> None:
    if not group:
        return
    setattr(order_obj, "ocaGroup", group)
    setattr(order_obj, "ocaType", int(oca_type))


def _is_ib_unset_double(value: float) -> bool:
    return value == UNSET_DOUBLE


def _build_stop_limit_order(
    *,
    side: OrderSide,
    qty: int,
    stop_price: float,
    limit_price: Optional[float],
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    client_tag: Optional[str],
) -> StopLimitOrder:
    resolved_limit_price = stop_price if limit_price is None else limit_price
    order = StopLimitOrder(side.value, qty, resolved_limit_price, stop_price, tif=tif)
    order.lmtPrice = resolved_limit_price
    order.auxPrice = stop_price
    order.outsideRth = outside_rth
    if account:
        order.account = account
    if client_tag:
        order.orderRef = client_tag
    return order


def _build_stop_order(
    *,
    side: OrderSide,
    qty: int,
    stop_price: float,
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    client_tag: Optional[str],
) -> StopOrder:
    order = StopOrder(side.value, qty, stop_price, tif=tif)
    order.auxPrice = stop_price
    order.outsideRth = outside_rth
    if account:
        order.account = account
    if client_tag:
        order.orderRef = client_tag
    return order


def _build_protective_stop_order(
    *,
    side: OrderSide,
    qty: int,
    stop_price: float,
    limit_price: Optional[float],
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    client_tag: Optional[str],
    use_stop_limit: bool,
    stop_limit_buffer_pct: float,
) -> StopOrder | StopLimitOrder:
    if use_stop_limit:
        resolved_limit_price = (
            limit_price
            if limit_price is not None and abs(limit_price - stop_price) > 1e-9
            else _outside_rth_stop_limit_price(
                side=side,
                stop_price=stop_price,
                buffer_pct=stop_limit_buffer_pct,
            )
        )
        return _build_stop_limit_order(
            side=side,
            qty=qty,
            stop_price=stop_price,
            limit_price=resolved_limit_price,
            tif=tif,
            outside_rth=outside_rth,
            account=account,
            client_tag=client_tag,
        )
    return _build_stop_order(
        side=side,
        qty=qty,
        stop_price=stop_price,
        tif=tif,
        outside_rth=outside_rth,
        account=account,
        client_tag=client_tag,
    )


def _is_stop_limit_order(order_obj: object) -> bool:
    order_type = getattr(order_obj, "orderType", None)
    if order_type is None:
        return False
    normalized = str(order_type).strip().upper().replace(" ", "")
    return normalized in {"STPLMT", "STOPLIMIT"}


def _stop_kind(order_obj: object) -> str:
    return "STP_LMT" if _is_stop_limit_order(order_obj) else "STP"


def _uses_stop_limit(outside_rth: bool) -> bool:
    return bool(outside_rth)


def _outside_rth_stop_limit_price(*, side: OrderSide, stop_price: float, buffer_pct: float) -> float:
    normalized_pct = max(buffer_pct, 0.0)
    if normalized_pct <= 0:
        return stop_price
    delta = stop_price * normalized_pct
    if side == OrderSide.SELL:
        candidate = stop_price - delta
    else:
        candidate = stop_price + delta
    if not math.isfinite(candidate) or candidate <= 0:
        return stop_price
    return candidate


def _outside_rth_stop_limit_buffer_pct_from_env() -> float:
    raw = os.getenv("APPS_OUTSIDE_RTH_STOP_BUFFER_PCT", "0.01")
    try:
        parsed = float(raw)
    except ValueError:
        return 0.01
    if not math.isfinite(parsed):
        return 0.01
    if parsed < 0:
        return 0.0
    return parsed


def _publish_stop_mode_selected(
    *,
    event_bus: EventBus | None,
    symbol: str,
    parent_order_id: Optional[int],
    trade: Trade,
    session_phase: SessionPhase,
    stop_price: float,
    execution_mode: Optional[str],
    client_tag: Optional[str],
) -> None:
    if event_bus is None:
        return
    order = getattr(trade, "order", None)
    limit_price = _maybe_float(getattr(order, "lmtPrice", None)) if order is not None else None
    event_bus.publish(
        OrderStopModeSelected.now(
            symbol=symbol,
            parent_order_id=parent_order_id,
            order_id=_trade_order_id(trade),
            session_phase=session_phase.value,
            stop_kind=_stop_kind(order),
            stop_price=stop_price,
            limit_price=limit_price,
            execution_mode=execution_mode,
            client_tag=client_tag,
        )
    )


def _is_trade_filled(trade_obj: Trade, expected_qty: int) -> bool:
    order_status = getattr(trade_obj, "orderStatus", None)
    if not order_status:
        return False
    status = _normalize_status(getattr(order_status, "status", None))
    if status != "filled":
        return False
    filled = _maybe_float(getattr(order_status, "filled", None))
    if filled is not None and filled < float(expected_qty):
        return False
    remaining = _maybe_float(getattr(order_status, "remaining", None))
    if remaining is not None and remaining > 0:
        return False
    return True


def _is_trade_inactive(trade_obj: Trade) -> bool:
    order_status = getattr(trade_obj, "orderStatus", None)
    if order_status is None:
        return False
    status = _normalize_status(getattr(order_status, "status", None))
    return status in _INACTIVE_ORDER_STATUSES


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

    attach_trade_events(
        trade,
        on_status=lambda trade_obj, *_args: _publish_status(trade_obj),
        on_filled=lambda trade_obj, *_args: _publish_fill(trade_obj),
        on_fill=lambda trade_obj, *_args: _publish_fill(trade_obj),
    )


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
    last_fill: (
        tuple[
            Optional[float],
            Optional[float],
            Optional[float],
            Optional[float],
            Optional[float],
            Optional[str],
        ]
        | None
    ) = None
    snapshot_reported = False
    qty_mismatch_reported = False
    expected_qty = float(qty)

    def _publish_snapshot_if_needed(trade_obj: Trade, *, status: Optional[str]) -> None:
        nonlocal snapshot_reported
        if snapshot_reported:
            return
        snapshot_reported = True
        broker_order_qty = _maybe_float(getattr(trade_obj.order, "totalQuantity", None))
        event_bus.publish(
            BracketChildOrderBrokerSnapshot.now(
                kind=kind,
                symbol=symbol,
                side=side,
                expected_qty=expected_qty,
                broker_order_qty=broker_order_qty,
                order_id=trade_obj.order.orderId,
                parent_order_id=parent_order_id,
                status=status,
                client_tag=client_tag,
            )
        )

    def _publish_mismatch_if_needed(trade_obj: Trade, *, status: Optional[str]) -> None:
        nonlocal qty_mismatch_reported
        if qty_mismatch_reported:
            return
        broker_order_qty = _maybe_float(getattr(trade_obj.order, "totalQuantity", None))
        if broker_order_qty is None or not _has_qty_mismatch(expected_qty, broker_order_qty):
            return
        qty_mismatch_reported = True
        event_bus.publish(
            BracketChildQuantityMismatchDetected.now(
                kind=kind,
                symbol=symbol,
                side=side,
                expected_qty=expected_qty,
                broker_order_qty=broker_order_qty,
                order_id=trade_obj.order.orderId,
                parent_order_id=parent_order_id,
                status=status,
                client_tag=client_tag,
            )
        )

    def _publish_status(trade_obj: Trade) -> None:
        nonlocal last_status
        status = trade_obj.orderStatus.status
        if not status or status == last_status:
            return
        last_status = status
        _publish_snapshot_if_needed(trade_obj, status=status)
        _publish_mismatch_if_needed(trade_obj, status=status)
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
        broker_order_qty = _maybe_float(getattr(trade_obj.order, "totalQuantity", None))
        filled_qty_raw = _maybe_float(order_status.filled)
        avg_fill_price = _maybe_float(order_status.avgFillPrice)
        remaining_qty_raw = _maybe_float(order_status.remaining)
        filled_qty = filled_qty_raw
        remaining_qty = remaining_qty_raw
        if filled_qty is not None:
            filled_qty = min(max(filled_qty, 0.0), float(qty))
        if remaining_qty is not None:
            remaining_qty = min(max(remaining_qty, 0.0), float(qty))
        status = order_status.status
        _publish_snapshot_if_needed(trade_obj, status=status)
        _publish_mismatch_if_needed(trade_obj, status=status)
        nonlocal last_fill
        snapshot = (
            broker_order_qty,
            filled_qty_raw,
            avg_fill_price,
            remaining_qty_raw,
            filled_qty,
            status,
        )
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
                expected_qty=expected_qty,
                broker_order_qty=broker_order_qty,
                broker_filled_qty_raw=filled_qty_raw,
                broker_remaining_qty_raw=remaining_qty_raw,
                filled_qty=filled_qty,
                avg_fill_price=avg_fill_price,
                remaining_qty=remaining_qty,
                client_tag=client_tag,
            )
        )

    order_status = getattr(trade, "orderStatus", None)
    _publish_snapshot_if_needed(
        trade,
        status=getattr(order_status, "status", None),
    )
    _publish_mismatch_if_needed(
        trade,
        status=getattr(order_status, "status", None),
    )

    attach_trade_events(
        trade,
        on_status=lambda trade_obj, *_args: _publish_status(trade_obj),
        on_filled=lambda trade_obj, *_args: _publish_fill(trade_obj),
        on_fill=lambda trade_obj, *_args: _publish_fill(trade_obj),
    )


def _attach_stop_trigger_reprice(
    trade: Trade,
    *,
    ib: IB,
    contract: Stock,
    side: OrderSide,
    qty: int,
    stop_price: float,
    symbol: str,
    parent_order_id: Optional[int],
    client_tag: Optional[str],
    event_bus: EventBus | None,
    loop: asyncio.AbstractEventLoop,
    scheduler: Optional[
        Callable[
            [asyncio.AbstractEventLoop, Coroutine[object, object, None]],
            Optional[_ScheduledHandle],
        ]
    ] = None,
    on_replaced: Optional[Callable[[Trade], None]] = None,
) -> None:
    last_status: Optional[str] = None
    reprice_pending = False
    reprice_applied = False

    async def _reprice(trade_obj: Trade) -> None:
        nonlocal reprice_pending, reprice_applied
        try:
            if _is_trade_inactive(trade_obj):
                reprice_applied = True
                return
            touch_price = await _touch_price_for_stop_limit(ib, contract, side)
            if touch_price is None:
                return
            current_stop_price = _maybe_float(getattr(trade_obj.order, "auxPrice", None))
            if current_stop_price is None or current_stop_price <= 0:
                current_stop_price = stop_price
            limit_price = _safe_triggered_limit_price(
                side=side,
                stop_price=current_stop_price,
                touch_price=touch_price,
            )
            order = copy.copy(trade_obj.order)
            order.orderId = trade_obj.order.orderId
            order.lmtPrice = limit_price
            order.auxPrice = current_stop_price
            if _is_trade_inactive(trade_obj):
                reprice_applied = True
                return
            try:
                updated_trade = _place_order_sanitized(ib, contract, order)
            except AssertionError:
                reprice_applied = True
                return
            reprice_applied = True
            if updated_trade is not trade_obj:
                if on_replaced is not None:
                    on_replaced(updated_trade)
                elif event_bus:
                    _attach_bracket_child_handlers(
                        updated_trade,
                        kind="stop_loss",
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        price=stop_price,
                        parent_order_id=parent_order_id,
                        client_tag=client_tag,
                        event_bus=event_bus,
                    )
        finally:
            reprice_pending = False

    def _publish_status(trade_obj: Trade) -> None:
        nonlocal last_status, reprice_pending
        status = _normalize_status(getattr(trade_obj.orderStatus, "status", None))
        if not status:
            return
        previous = last_status
        last_status = status
        if reprice_applied or reprice_pending:
            return
        # IBKR stop-limit orders move from PreSubmitted to Submitted when the stop is elected.
        # TODO: verify this transition across all target venues; some routes may not emit PreSubmitted.
        if previous != "presubmitted" or status != "submitted":
            return
        reprice_pending = True
        if scheduler is None:
            _schedule_coroutine(loop, _reprice(trade_obj))
            return
        scheduler(loop, _reprice(trade_obj))

    def _publish_fill(_trade_obj: Trade) -> None:
        nonlocal reprice_applied
        reprice_applied = True

    attach_trade_events(
        trade,
        on_status=lambda trade_obj, *_args: _publish_status(trade_obj),
        on_filled=lambda trade_obj, *_args: _publish_fill(trade_obj),
        on_fill=lambda trade_obj, *_args: _publish_fill(trade_obj),
    )


def _maybe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return None


def _maybe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return None


def _has_qty_mismatch(expected_qty: float, broker_order_qty: float) -> bool:
    return abs(expected_qty - broker_order_qty) > 1e-9


def _extract_execution_id(fill_obj: object | None) -> Optional[str]:
    if fill_obj is None:
        return None
    execution = getattr(fill_obj, "execution", None)
    if execution is None:
        return None
    raw = getattr(execution, "execId", None)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return text


def _extract_execution_shares(fill_obj: object | None) -> Optional[float]:
    if fill_obj is None:
        return None
    execution = getattr(fill_obj, "execution", None)
    if execution is None:
        return None
    shares = _maybe_float(getattr(execution, "shares", None))
    if shares is None:
        return None
    return shares


def _normalize_status(value: object) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _outside_rth_for_session_phase(phase: SessionPhase) -> bool:
    return phase != SessionPhase.RTH


def _consume_scheduled_coroutine_result(handle: _ScheduledHandle) -> None:
    try:
        handle.result()
    except (asyncio.CancelledError, ThreadFutureCancelledError):
        return
    except Exception as exc:
        if isinstance(handle, asyncio.Task):
            loop = handle.get_loop()
            if not loop.is_closed():
                loop.call_exception_handler(
                    {
                        "message": "Detached broker callback coroutine failed",
                        "exception": exc,
                        "task": handle,
                    }
                )
        return


def _schedule_coroutine(
    loop: asyncio.AbstractEventLoop,
    coro: Coroutine[object, object, None],
) -> Optional[_ScheduledHandle]:
    if loop.is_closed():
        coro.close()
        return None
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is loop:
        try:
            handle: _ScheduledHandle = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            return None
    else:
        try:
            handle = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            return None
    handle.add_done_callback(_consume_scheduled_coroutine_result)
    return handle


async def _touch_price_for_stop_limit(ib: IB, contract: Stock, side: OrderSide) -> Optional[float]:
    ticker = ib.ticker(contract)
    touch_price = _touch_price_from_ticker(ticker, side)
    if touch_price is not None:
        return touch_price
    try:
        snapshot = await req_tickers_snapshot(ib, contract)
    except Exception:
        return None
    return _touch_price_from_ticker(snapshot, side)


def _touch_price_from_ticker(ticker: object, side: OrderSide) -> Optional[float]:
    if ticker is None:
        return None
    raw_price = getattr(ticker, "bid", None) if side == OrderSide.SELL else getattr(ticker, "ask", None)
    price = _maybe_float(raw_price)
    if price is None or not math.isfinite(price) or price <= 0:
        return None
    return price


def _safe_triggered_limit_price(*, side: OrderSide, stop_price: float, touch_price: float) -> float:
    if side == OrderSide.SELL:
        return min(touch_price, stop_price)
    return max(touch_price, stop_price)

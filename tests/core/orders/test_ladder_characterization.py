from __future__ import annotations

import asyncio

import pytest

from apps.core.orders.models import (
    LadderExecutionMode,
    LadderOrderSpec,
    OrderAck,
    OrderCancelSpec,
    OrderReplaceSpec,
    OrderSide,
    OrderSpec,
    OrderType,
)
from apps.core.orders.service import OrderService, OrderValidationError


class _FakeOrderPort:
    def __init__(self) -> None:
        self.ladder_specs: list[LadderOrderSpec] = []

    async def prewarm_session_phase(self, *, symbol: str, exchange: str = "SMART", currency: str = "USD") -> None:
        return None

    def clear_session_phase_cache(self) -> None:
        return None

    async def submit_order(self, spec: OrderSpec) -> OrderAck:
        return OrderAck.now(order_id=1, status="submitted")

    async def cancel_order(self, spec: OrderCancelSpec) -> OrderAck:
        return OrderAck.now(order_id=spec.order_id, status="cancelled")

    async def replace_order(self, spec: OrderReplaceSpec) -> OrderAck:
        return OrderAck.now(order_id=spec.order_id, status="submitted")

    async def submit_bracket_order(self, spec) -> OrderAck:
        return OrderAck.now(order_id=1, status="submitted")

    async def submit_ladder_order(self, spec: LadderOrderSpec) -> OrderAck:
        self.ladder_specs.append(spec)
        return OrderAck.now(order_id=1, status="submitted")


def _run(coro):
    return asyncio.run(coro)


def test_detached70_requires_exactly_two_take_profits() -> None:
    service = OrderService(_FakeOrderPort())
    spec = LadderOrderSpec(
        symbol="AAPL",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5, 12.0],
        take_profit_qtys=[6, 3, 1],
        stop_loss=9.5,
        stop_updates=[10.0, 11.0],
        execution_mode=LadderExecutionMode.DETACHED_70_30,
    )

    with pytest.raises(
        OrderValidationError,
        match="execution_mode DETACHED_70_30 requires exactly 2 take_profits",
    ):
        _run(service.submit_ladder(spec))


def test_detached70_requires_70_30_split() -> None:
    service = OrderService(_FakeOrderPort())
    spec = LadderOrderSpec(
        symbol="AAPL",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5],
        take_profit_qtys=[8, 2],
        stop_loss=9.5,
        stop_updates=[10.0],
        execution_mode=LadderExecutionMode.DETACHED_70_30,
    )

    with pytest.raises(
        OrderValidationError,
        match=r"execution_mode DETACHED_70_30 requires take_profit_qtys=\[7, 3\]",
    ):
        _run(service.submit_ladder(spec))


def test_detached70_accepts_expected_split_and_submits() -> None:
    port = _FakeOrderPort()
    service = OrderService(port)
    spec = LadderOrderSpec(
        symbol="aapl",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5],
        take_profit_qtys=[7, 3],
        stop_loss=9.5,
        stop_updates=[10.0],
        execution_mode=LadderExecutionMode.DETACHED_70_30,
    )

    ack = _run(service.submit_ladder(spec))

    assert ack.order_id == 1
    assert len(port.ladder_specs) == 1
    assert port.ladder_specs[0].symbol == "AAPL"


def test_detached_requires_exactly_three_take_profits() -> None:
    service = OrderService(_FakeOrderPort())
    spec = LadderOrderSpec(
        symbol="AAPL",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5],
        take_profit_qtys=[7, 3],
        stop_loss=9.5,
        stop_updates=[10.0],
        execution_mode=LadderExecutionMode.DETACHED,
    )

    with pytest.raises(
        OrderValidationError,
        match="execution_mode DETACHED requires exactly 3 take_profits",
    ):
        _run(service.submit_ladder(spec))


def test_attached_ladder_mode_is_rejected() -> None:
    service = OrderService(_FakeOrderPort())
    spec = LadderOrderSpec(
        symbol="AAPL",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5, 12.0],
        take_profit_qtys=[6, 3, 1],
        stop_loss=9.5,
        stop_updates=[10.0, 11.0],
        execution_mode=LadderExecutionMode.ATTACHED,
    )

    with pytest.raises(
        OrderValidationError,
        match="execution_mode ATTACHED is not supported for ladders",
    ):
        _run(service.submit_ladder(spec))


def test_detached_accepts_three_take_profits_and_submits() -> None:
    port = _FakeOrderPort()
    service = OrderService(port)
    spec = LadderOrderSpec(
        symbol="aapl",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5, 12.0],
        take_profit_qtys=[6, 3, 1],
        stop_loss=9.5,
        stop_updates=[10.0, 11.0],
        execution_mode=LadderExecutionMode.DETACHED,
    )

    ack = _run(service.submit_ladder(spec))

    assert ack.order_id == 1
    assert len(port.ladder_specs) == 1
    assert port.ladder_specs[0].symbol == "AAPL"


def test_detached_requires_two_stop_updates_for_three_take_profits() -> None:
    service = OrderService(_FakeOrderPort())
    spec = LadderOrderSpec(
        symbol="AAPL",
        qty=10,
        side=OrderSide.BUY,
        entry_type=OrderType.MARKET,
        take_profits=[11.0, 11.5, 12.0],
        take_profit_qtys=[6, 3, 1],
        stop_loss=9.5,
        stop_updates=[10.0],
        execution_mode=LadderExecutionMode.DETACHED,
    )

    with pytest.raises(
        OrderValidationError,
        match="stop_updates must match take_profit count minus one",
    ):
        _run(service.submit_ladder(spec))

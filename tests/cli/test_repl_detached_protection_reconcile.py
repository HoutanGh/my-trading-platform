from __future__ import annotations

import asyncio
import importlib.util
from importlib.machinery import ModuleSpec
import sys
import types

if importlib.util.find_spec("ib_insync") is None:
    ib_insync_stub = types.ModuleType("ib_insync")
    ib_insync_stub.__path__ = []  # type: ignore[attr-defined]
    ib_insync_stub.__spec__ = ModuleSpec("ib_insync", loader=None)

    def _stub_type(name: str):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    ib_insync_stub.IB = _stub_type("IB")
    ib_insync_stub.Stock = _stub_type("Stock")
    ib_insync_stub.Ticker = _stub_type("Ticker")
    ib_insync_stub.BarData = _stub_type("BarData")
    ib_insync_stub.Trade = _stub_type("Trade")
    ib_insync_stub.MarketOrder = _stub_type("MarketOrder")
    ib_insync_stub.LimitOrder = _stub_type("LimitOrder")
    ib_insync_stub.StopOrder = _stub_type("StopOrder")
    ib_insync_stub.StopLimitOrder = _stub_type("StopLimitOrder")
    sys.modules["ib_insync"] = ib_insync_stub
    ib_insync_util_stub = types.ModuleType("ib_insync.util")
    ib_insync_util_stub.UNSET_DOUBLE = float("nan")
    sys.modules["ib_insync.util"] = ib_insync_util_stub

from apps.cli.repl import REPL
from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.ops.events import (
    DetachedProtectionCoverageGapDetected,
    DetachedProtectionReconciliationCompleted,
    DetachedSessionRestoreCompleted,
    DetachedSessionRestored,
)
from apps.core.positions.models import PositionSnapshot


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


class _FakeConnectionConfig:
    timeout = 2.0


class _FakeConnection:
    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self.config = _FakeConnectionConfig()
        self.ib = object()

    def status(self) -> dict[str, object]:
        return {"connected": self._connected}


class _FakeActiveOrdersService:
    def __init__(self, orders: list[ActiveOrderSnapshot]) -> None:
        self._orders = list(orders)

    async def list_active_orders(
        self,
        *,
        account: str | None = None,
        scope: str = "client",
    ) -> list[ActiveOrderSnapshot]:
        return list(self._orders)


class _FakePositionsService:
    def __init__(self, positions: list[PositionSnapshot]) -> None:
        self._positions = list(positions)

    async def list_positions(self, account: str | None = None) -> list[PositionSnapshot]:
        return list(self._positions)


def _position(*, account: str, symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        account=account,
        symbol=symbol,
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        qty=qty,
    )


def _stop_order(*, order_id: int, account: str, symbol: str, remaining_qty: float) -> ActiveOrderSnapshot:
    return ActiveOrderSnapshot(
        order_id=order_id,
        perm_id=None,
        parent_order_id=None,
        client_id=None,
        account=account,
        symbol=symbol,
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        side="SELL",
        order_type="STP",
        qty=remaining_qty,
        filled_qty=0.0,
        remaining_qty=remaining_qty,
        limit_price=None,
        stop_price=9.5,
        status="Submitted",
        tif="DAY",
        outside_rth=None,
        client_tag="breakout:AAPL:10",
    )


def _tp_order(*, order_id: int, account: str, symbol: str, remaining_qty: float) -> ActiveOrderSnapshot:
    return ActiveOrderSnapshot(
        order_id=order_id,
        perm_id=None,
        parent_order_id=None,
        client_id=None,
        account=account,
        symbol=symbol,
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        side="SELL",
        order_type="LMT",
        qty=remaining_qty,
        filled_qty=0.0,
        remaining_qty=remaining_qty,
        limit_price=11.0,
        stop_price=None,
        status="Submitted",
        tif="DAY",
        outside_rth=None,
        client_tag="breakout:AAPL:10",
    )


class _FakePositionOriginTracker:
    def __init__(self, *, tag: str | None = None) -> None:
        self._tag = tag

    def tag_for(self, account: str | None, symbol: str) -> str | None:
        return self._tag

    def take_profits_for(self, account: str | None, symbol: str) -> list[float] | None:
        if self._tag is None:
            return None
        return [11.0, 12.0]


def test_reconcile_detached_protection_publishes_gap_and_summary_events() -> None:
    tracker = _FakePositionOriginTracker(tag="breakout:AAPL:10")
    bus = _FakeEventBus()
    repl = REPL(
        _FakeConnection(connected=True),  # type: ignore[arg-type]
        positions_service=_FakePositionsService([_position(account="DU1", symbol="AAPL", qty=100)]),  # type: ignore[arg-type]
        active_orders_service=_FakeActiveOrdersService(
            [_stop_order(order_id=101, account="DU1", symbol="AAPL", remaining_qty=70)]
        ),  # type: ignore[arg-type]
        position_origin_tracker=tracker,  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
    )

    asyncio.run(repl._reconcile_detached_protection_coverage(trigger="connection_established"))

    gap_events = [event for event in bus.events if isinstance(event, DetachedProtectionCoverageGapDetected)]
    summary_events = [
        event for event in bus.events if isinstance(event, DetachedProtectionReconciliationCompleted)
    ]
    assert len(gap_events) == 1
    assert gap_events[0].uncovered_qty == 30
    assert gap_events[0].stop_order_ids == [101]
    assert len(summary_events) == 1
    assert summary_events[0].gap_count == 1
    assert summary_events[0].inspected_position_count == 1


def test_reconcile_detached_protection_skips_when_disconnected() -> None:
    bus = _FakeEventBus()
    repl = REPL(
        _FakeConnection(connected=False),  # type: ignore[arg-type]
        positions_service=_FakePositionsService([_position(account="DU1", symbol="AAPL", qty=100)]),  # type: ignore[arg-type]
        active_orders_service=_FakeActiveOrdersService(
            [_stop_order(order_id=101, account="DU1", symbol="AAPL", remaining_qty=100)]
        ),  # type: ignore[arg-type]
        position_origin_tracker=_FakePositionOriginTracker(tag="breakout:AAPL:10"),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
    )

    asyncio.run(repl._reconcile_detached_protection_coverage(trigger="connection_established"))

    assert bus.events == []


def test_restore_detached_sessions_publishes_restored_and_summary_events() -> None:
    bus = _FakeEventBus()
    repl = REPL(
        _FakeConnection(connected=True),  # type: ignore[arg-type]
        positions_service=_FakePositionsService([_position(account="DU1", symbol="AAPL", qty=100)]),  # type: ignore[arg-type]
        active_orders_service=_FakeActiveOrdersService(
            [
                _stop_order(order_id=101, account="DU1", symbol="AAPL", remaining_qty=100),
                _tp_order(order_id=102, account="DU1", symbol="AAPL", remaining_qty=70),
            ]
        ),  # type: ignore[arg-type]
        position_origin_tracker=_FakePositionOriginTracker(tag="breakout:AAPL:10"),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
    )

    asyncio.run(repl._restore_detached_sessions(trigger="connection_established"))

    restored_events = [event for event in bus.events if isinstance(event, DetachedSessionRestored)]
    summary_events = [event for event in bus.events if isinstance(event, DetachedSessionRestoreCompleted)]
    assert len(restored_events) == 1
    assert restored_events[0].execution_mode == "detached70"
    assert restored_events[0].state == "protected"
    assert len(summary_events) == 1
    assert summary_events[0].restored_count == 1
    assert summary_events[0].protected_count == 1

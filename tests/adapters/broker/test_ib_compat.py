from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import datetime, timezone
import sys
from types import SimpleNamespace
import types
from typing import Any

_IB_CLIENT_MODULE = "apps.adapters.broker._ib_client"

try:
    importlib.import_module(_IB_CLIENT_MODULE)
except Exception:
    ib_client_stub = types.ModuleType(_IB_CLIENT_MODULE)

    def _stub_type(name: str):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    def _parse_ib_datetime(_value: object) -> datetime:
        return datetime.now(timezone.utc)

    ib_client_stub.IB = _stub_type("IB")
    ib_client_stub.Stock = _stub_type("Stock")
    ib_client_stub.Ticker = _stub_type("Ticker")
    ib_client_stub.BarData = _stub_type("BarData")
    ib_client_stub.Trade = _stub_type("Trade")
    ib_client_stub.MarketOrder = _stub_type("MarketOrder")
    ib_client_stub.LimitOrder = _stub_type("LimitOrder")
    ib_client_stub.StopOrder = _stub_type("StopOrder")
    ib_client_stub.StopLimitOrder = _stub_type("StopLimitOrder")
    ib_client_stub.IB_CLIENT_BACKEND = "test-stub"
    ib_client_stub.UNSET_DOUBLE = float("nan")
    ib_client_stub.parse_ib_datetime = _parse_ib_datetime
    sys.modules[_IB_CLIENT_MODULE] = ib_client_stub

from apps.adapters.broker._ib_compat import (
    attach_bar_update_event,
    attach_trade_events,
    detach_bar_update_event,
    req_tickers_snapshot,
    silence_ib_client_loggers,
    what_if_order,
)


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler: Any):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self


def test_attach_trade_events_registers_available_handlers() -> None:
    trade = SimpleNamespace(
        statusEvent=_FakeEvent(),
        filledEvent=_FakeEvent(),
        fillEvent=_FakeEvent(),
    )
    attached = attach_trade_events(
        trade,
        on_status=lambda *_args: None,
        on_filled=lambda *_args: None,
        on_fill=lambda *_args: None,
    )

    assert set(attached) == {"statusEvent", "filledEvent", "fillEvent"}
    assert len(trade.statusEvent.handlers) == 1
    assert len(trade.filledEvent.handlers) == 1
    assert len(trade.fillEvent.handlers) == 1


def test_attach_trade_events_ignores_missing_events() -> None:
    trade = object()
    attached = attach_trade_events(trade, on_status=lambda *_args: None)
    assert attached == ()


def test_attach_and_detach_bar_update_event() -> None:
    bars = SimpleNamespace(updateEvent=_FakeEvent())

    def _handler(*_args: object) -> None:
        return None

    assert attach_bar_update_event(bars, _handler)
    assert len(bars.updateEvent.handlers) == 1
    assert detach_bar_update_event(bars, _handler)
    assert bars.updateEvent.handlers == []


def test_silence_ib_client_loggers_applies_requested_logger_names() -> None:
    logger_name = "apps.tests.ib_compat.logger"
    logger = logging.getLogger(logger_name)
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = True

    applied = silence_ib_client_loggers(logger_names=[logger_name])

    assert applied == (logger_name,)
    assert logger.level == logging.CRITICAL
    assert logger.propagate is False
    assert logger.handlers


def test_req_tickers_snapshot_uses_req_tickers_async_when_available() -> None:
    ticker = SimpleNamespace(bid=1.0, ask=1.1)

    class _FakeIb:
        async def reqTickersAsync(self, _contract: object) -> list[object]:
            return [ticker]

    result = asyncio.run(req_tickers_snapshot(_FakeIb(), object(), timeout=0.1))
    assert result is ticker


def test_req_tickers_snapshot_falls_back_to_req_mkt_data() -> None:
    contract = object()
    ticker = SimpleNamespace(bid=1.0, ask=None)
    cancelled: list[object] = []

    class _FakeIb:
        def reqMktData(self, _contract: object, *_args: object, **_kwargs: object) -> object:
            return ticker

        def cancelMktData(self, item: object) -> None:
            cancelled.append(item)

    result = asyncio.run(req_tickers_snapshot(_FakeIb(), contract, timeout=0.1, poll_interval=0.0))
    assert result is ticker
    assert cancelled == [contract]


def test_what_if_order_prefers_low_level_client_when_available() -> None:
    suppressed: list[int] = []

    class _FakeClient:
        def __init__(self) -> None:
            self.placed: list[tuple[int, object, object]] = []

        def getReqId(self) -> int:
            return 42

        def placeOrder(self, req_id: int, contract: object, order: object) -> None:
            self.placed.append((req_id, contract, order))

    class _FakeWrapper:
        def startReq(self, _req_id: int, _contract: object):
            async def _awaitable() -> object:
                return SimpleNamespace(status="Submitted")

            return _awaitable()

    fake_ib = SimpleNamespace(client=_FakeClient(), wrapper=_FakeWrapper())
    contract = object()
    order = SimpleNamespace(whatIf=False)

    state, req_id = asyncio.run(
        what_if_order(
            fake_ib,
            contract,
            order,
            timeout=0.1,
            suppress_req_id=lambda value: suppressed.append(value),
        )
    )

    assert req_id == 42
    assert suppressed == [42]
    assert state.status == "Submitted"
    assert len(fake_ib.client.placed) == 1
    placed_req_id, placed_contract, placed_order = fake_ib.client.placed[0]
    assert placed_req_id == 42
    assert placed_contract is contract
    assert placed_order is not order
    assert getattr(placed_order, "whatIf", None) is True


def test_what_if_order_falls_back_to_what_if_order_async() -> None:
    class _FakeIb:
        async def whatIfOrderAsync(self, contract: object, order: object) -> object:
            return SimpleNamespace(contract=contract, order=order, status="Submitted")

    contract = object()
    order = SimpleNamespace(whatIf=False)
    state, req_id = asyncio.run(what_if_order(_FakeIb(), contract, order, timeout=0.1))
    assert req_id is None
    assert state.contract is contract
    assert state.order is order

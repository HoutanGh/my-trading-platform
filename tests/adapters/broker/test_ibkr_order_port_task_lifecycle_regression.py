from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
import sys
import types

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

from apps.adapters.broker.ibkr_order_port import IBKROrderPort, _schedule_coroutine


async def _noop() -> None:
    await asyncio.sleep(0)


def test_schedule_coroutine_returns_handle_for_shutdown_management() -> None:
    async def _run() -> object:
        loop = asyncio.get_running_loop()
        handle = _schedule_coroutine(loop, _noop())
        await asyncio.sleep(0)
        return handle

    handle = asyncio.run(_run())
    assert handle is not None


def test_schedule_coroutine_closes_coro_when_loop_closed() -> None:
    loop = asyncio.new_event_loop()
    loop.close()
    coro = _noop()
    handle = _schedule_coroutine(loop, coro)
    assert handle is None
    assert coro.cr_frame is None


class _DummyConnection:
    def __init__(self) -> None:
        self.ib = object()


def test_managed_scheduler_cancels_pending_tasks_on_close() -> None:
    async def _run() -> None:
        port = IBKROrderPort(_DummyConnection())  # type: ignore[arg-type]
        loop = asyncio.get_running_loop()
        running = asyncio.Event()
        cancelled = asyncio.Event()

        async def _pending() -> None:
            running.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        handle = port._schedule_managed_coroutine(loop, _pending())
        assert handle is not None
        await running.wait()
        port.close()
        await asyncio.sleep(0)
        assert cancelled.is_set()

    asyncio.run(_run())

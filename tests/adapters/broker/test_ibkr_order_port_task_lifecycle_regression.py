from __future__ import annotations

import asyncio
import importlib.util
from importlib.machinery import ModuleSpec
import sys
import types

import pytest

if importlib.util.find_spec("ib_insync") is None:
    ib_insync_stub = types.ModuleType("ib_insync")
    ib_insync_stub.__path__ = []  # type: ignore[attr-defined]
    ib_insync_stub.__spec__ = ModuleSpec("ib_insync", loader=None)

    def _stub_type(name: str):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    ib_insync_stub.IB = _stub_type("IB")
    ib_insync_stub.Stock = _stub_type("Stock")
    ib_insync_stub.Trade = _stub_type("Trade")
    ib_insync_stub.MarketOrder = _stub_type("MarketOrder")
    ib_insync_stub.LimitOrder = _stub_type("LimitOrder")
    ib_insync_stub.StopOrder = _stub_type("StopOrder")
    ib_insync_stub.StopLimitOrder = _stub_type("StopLimitOrder")
    sys.modules["ib_insync"] = ib_insync_stub
    ib_insync_util_stub = types.ModuleType("ib_insync.util")
    ib_insync_util_stub.__spec__ = ModuleSpec("ib_insync.util", loader=None)
    ib_insync_util_stub.UNSET_DOUBLE = float("nan")
    sys.modules["ib_insync.util"] = ib_insync_util_stub

from apps.adapters.broker.ibkr_order_port import _schedule_coroutine


async def _noop() -> None:
    await asyncio.sleep(0)


@pytest.mark.xfail(
    reason=(
        "Known task-lifecycle gap: _schedule_coroutine fire-and-forgets tasks and does not "
        "return a handle that callers can track/cancel during shutdown."
    ),
    strict=True,
)
def test_schedule_coroutine_returns_handle_for_shutdown_management() -> None:
    async def _run() -> object:
        loop = asyncio.get_running_loop()
        handle = _schedule_coroutine(loop, _noop())
        await asyncio.sleep(0)
        return handle

    handle = asyncio.run(_run())
    assert handle is not None

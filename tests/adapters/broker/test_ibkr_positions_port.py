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

from apps.adapters.broker.ibkr_positions_port import (
    _enrich_with_single_position_pnl,
    _maybe_pnl_float,
)
from apps.core.positions.models import PositionSnapshot


class _FakeIb:
    def __init__(self, pnl_by_con_id: dict[int, tuple[object, object]]) -> None:
        self._pnl_by_con_id = pnl_by_con_id
        self.req_calls: list[tuple[str, str, int]] = []
        self.cancel_calls: list[tuple[str, str, int]] = []

    def reqPnLSingle(self, account: str, model_code: str, con_id: int):
        self.req_calls.append((account, model_code, con_id))
        realized, unrealized = self._pnl_by_con_id.get(con_id, (None, None))
        return types.SimpleNamespace(realizedPnL=realized, unrealizedPnL=unrealized)

    def cancelPnLSingle(self, account: str, model_code: str, con_id: int) -> None:
        self.cancel_calls.append((account, model_code, con_id))


def _snapshot(
    *,
    symbol: str,
    con_id: int | None,
    realized_pnl: float | None = None,
    unrealized_pnl: float | None = None,
) -> PositionSnapshot:
    return PositionSnapshot(
        account="DU123456",
        symbol=symbol,
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        qty=1.0,
        avg_cost=10.0,
        con_id=con_id,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
    )


def test_enrich_with_single_position_pnl_populates_missing_fields() -> None:
    fake_ib = _FakeIb(
        {
            101: (12.25, 1.5),
            202: (-3.0, -0.25),
        }
    )
    snapshots = [
        _snapshot(symbol="AAPL", con_id=101),
        _snapshot(symbol="TSLA", con_id=202),
        _snapshot(symbol="NVDA", con_id=None),
    ]

    enriched = asyncio.run(
        _enrich_with_single_position_pnl(
            fake_ib,
            snapshots,
            account="DU123456",
            wait_seconds=0.0,
        )
    )

    assert enriched[0].realized_pnl == 12.25
    assert enriched[0].unrealized_pnl == 1.5
    assert enriched[1].realized_pnl == -3.0
    assert enriched[1].unrealized_pnl == -0.25
    assert enriched[2].realized_pnl is None
    assert fake_ib.req_calls == [
        ("DU123456", "", 101),
        ("DU123456", "", 202),
    ]
    assert fake_ib.cancel_calls == [
        ("DU123456", "", 101),
        ("DU123456", "", 202),
    ]


def test_enrich_with_single_position_pnl_ignores_nan_values() -> None:
    fake_ib = _FakeIb({101: (float("nan"), float("nan"))})
    snapshots = [_snapshot(symbol="AAPL", con_id=101)]

    enriched = asyncio.run(
        _enrich_with_single_position_pnl(
            fake_ib,
            snapshots,
            account="DU123456",
            wait_seconds=0.0,
        )
    )

    assert enriched[0].realized_pnl is None
    assert enriched[0].unrealized_pnl is None


def test_maybe_pnl_float_filters_non_finite_values() -> None:
    assert _maybe_pnl_float(1.25) == 1.25
    assert _maybe_pnl_float(float("nan")) is None
    assert _maybe_pnl_float(float("inf")) is None

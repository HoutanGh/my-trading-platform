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
    assert _maybe_pnl_float(1.7976931348623157e308) is None

from __future__ import annotations

from apps.cli.positions_realized_table import format_positions_realized_table
from apps.core.positions.models import PositionSnapshot


def _position(
    *,
    account: str = "DU123456",
    symbol: str,
    sec_type: str,
    qty: float = 1.0,
    realized_pnl: float | None = None,
) -> PositionSnapshot:
    return PositionSnapshot(
        account=account,
        symbol=symbol,
        sec_type=sec_type,
        exchange="SMART",
        currency="USD",
        qty=qty,
        realized_pnl=realized_pnl,
    )


def test_format_positions_realized_table_filters_and_totals() -> None:
    lines = format_positions_realized_table(
        [
            _position(symbol="AAPL", sec_type="STK", qty=10, realized_pnl=12.5),
            _position(symbol="TSLA", sec_type="STK", qty=5, realized_pnl=-3.0),
            _position(symbol="EURUSD", sec_type="CASH", qty=1, realized_pnl=100.0),
            _position(symbol="MSFT", sec_type="STK", qty=3, realized_pnl=None),
        ]
    )

    assert lines
    assert "p&l" in lines[0]
    assert "qty" not in lines[0]
    assert "AAPL" in lines[2]
    assert "TSLA" in lines[3]
    assert "EURUSD" not in "\n".join(lines)
    assert "MSFT" not in "\n".join(lines)
    assert "TOTAL" in lines[4]
    assert "9.5" in lines[4]


def test_format_positions_realized_table_sort_and_min_filter() -> None:
    lines = format_positions_realized_table(
        [
            _position(symbol="AAPL", sec_type="STK", qty=10, realized_pnl=12.5),
            _position(symbol="TSLA", sec_type="STK", qty=5, realized_pnl=-3.0),
            _position(symbol="NVDA", sec_type="STK", qty=1, realized_pnl=2.0),
        ],
        sort_desc=False,
        min_realized=0.0,
    )

    assert lines
    assert "NVDA" in lines[2]
    assert "AAPL" in lines[3]
    assert "TSLA" not in "\n".join(lines)
    assert "14.5" in lines[4]


def test_format_positions_realized_table_returns_empty_when_no_matches() -> None:
    lines = format_positions_realized_table(
        [
            _position(symbol="EURUSD", sec_type="CASH", qty=1, realized_pnl=10.0),
            _position(symbol="AAPL", sec_type="STK", qty=10, realized_pnl=None),
        ]
    )

    assert lines == []


def test_format_positions_realized_table_ignores_nan_realized_values() -> None:
    lines = format_positions_realized_table(
        [
            _position(symbol="AAPL", sec_type="STK", qty=10, realized_pnl=float("nan")),
        ]
    )

    assert lines == []


def test_format_positions_realized_table_ignores_ib_unset_double() -> None:
    lines = format_positions_realized_table(
        [
            _position(symbol="OPEN", sec_type="STK", qty=10, realized_pnl=1.7976931348623157e308),
            _position(symbol="RXT", sec_type="STK", qty=1, realized_pnl=90.8635),
        ]
    )

    assert lines
    joined = "\n".join(lines)
    assert "OPEN" not in joined
    assert "RXT" in joined

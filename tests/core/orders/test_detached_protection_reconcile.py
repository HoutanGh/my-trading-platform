from __future__ import annotations

from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.orders.detached_protection_reconcile import reconcile_detached_protection_coverage
from apps.core.positions.models import PositionSnapshot


def _position(*, account: str, symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        account=account,
        symbol=symbol,
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        qty=qty,
    )


def _order(
    *,
    order_id: int,
    account: str,
    symbol: str,
    side: str = "SELL",
    order_type: str = "STP",
    remaining_qty: float = 0.0,
    qty: float | None = None,
    filled_qty: float | None = None,
    status: str = "Submitted",
    client_tag: str | None = "breakout:AAPL:10",
) -> ActiveOrderSnapshot:
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
        side=side,
        order_type=order_type,
        qty=qty,
        filled_qty=filled_qty,
        remaining_qty=remaining_qty,
        limit_price=None,
        stop_price=9.5,
        status=status,
        tif="DAY",
        outside_rth=None,
        client_tag=client_tag,
    )


def _tag_lookup(account: str | None, symbol: str) -> str | None:
    key = ((account or "").rstrip("."), symbol.upper())
    tags = {
        ("DU1", "AAPL"): "breakout:AAPL:10",
        ("DU1", "MSFT"): "manual:MSFT",
    }
    return tags.get(key)


def test_reconcile_detects_gap_for_breakout_tagged_long_position() -> None:
    report = reconcile_detached_protection_coverage(
        positions=[_position(account="DU1", symbol="AAPL", qty=100)],
        active_orders=[
            _order(order_id=101, account="DU1", symbol="AAPL", remaining_qty=70),
        ],
        tag_for_position=_tag_lookup,
        required_tag_prefix="breakout:",
    )

    assert report.inspected_position_count == 1
    assert report.covered_position_count == 0
    assert report.gap_count == 1
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.account == "DU1"
    assert gap.symbol == "AAPL"
    assert gap.position_qty == 100
    assert gap.protected_qty == 70
    assert gap.uncovered_qty == 30
    assert gap.stop_order_ids == [101]
    assert gap.stop_order_count == 1


def test_reconcile_ignores_non_stop_and_inactive_orders_for_coverage() -> None:
    report = reconcile_detached_protection_coverage(
        positions=[_position(account="DU1", symbol="AAPL", qty=100)],
        active_orders=[
            _order(order_id=101, account="DU1", symbol="AAPL", order_type="LMT", remaining_qty=100),
            _order(
                order_id=102,
                account="DU1",
                symbol="AAPL",
                order_type="STP",
                remaining_qty=100,
                status="Cancelled",
            ),
            _order(order_id=103, account="DU1", symbol="AAPL", order_type="STP", remaining_qty=100),
        ],
        tag_for_position=_tag_lookup,
        required_tag_prefix="breakout:",
    )

    assert report.inspected_position_count == 1
    assert report.covered_position_count == 1
    assert report.gap_count == 0
    assert report.gaps == []


def test_reconcile_skips_positions_without_matching_required_tag_prefix() -> None:
    report = reconcile_detached_protection_coverage(
        positions=[
            _position(account="DU1", symbol="AAPL", qty=100),
            _position(account="DU1", symbol="MSFT", qty=100),
        ],
        active_orders=[
            _order(order_id=201, account="DU1", symbol="AAPL", remaining_qty=100),
            _order(
                order_id=202,
                account="DU1",
                symbol="MSFT",
                remaining_qty=0,
                client_tag="manual:MSFT",
            ),
        ],
        tag_for_position=_tag_lookup,
        required_tag_prefix="breakout:",
    )

    assert report.position_count == 2
    assert report.inspected_position_count == 1
    assert report.covered_position_count == 1
    assert report.gap_count == 0

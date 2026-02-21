from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.positions.models import PositionSnapshot

_INACTIVE_ORDER_STATUSES = {"inactive", "cancelled", "apicancelled", "filled"}
_PROTECTIVE_STOP_ORDER_TYPES = {"STP", "STOP", "STPLMT", "STOPLIMIT"}


@dataclass(frozen=True)
class DetachedProtectionGap:
    account: Optional[str]
    symbol: str
    client_tag: Optional[str]
    position_qty: float
    protected_qty: float
    uncovered_qty: float
    stop_order_ids: list[int]
    stop_order_count: int


@dataclass(frozen=True)
class DetachedProtectionReconciliationReport:
    active_order_count: int
    position_count: int
    inspected_position_count: int
    covered_position_count: int
    gap_count: int
    gaps: list[DetachedProtectionGap]


def reconcile_detached_protection_coverage(
    *,
    positions: Sequence[PositionSnapshot],
    active_orders: Sequence[ActiveOrderSnapshot],
    tag_for_position: Optional[Callable[[Optional[str], str], Optional[str]]] = None,
    required_tag_prefix: Optional[str] = "breakout:",
) -> DetachedProtectionReconciliationReport:
    stop_orders_by_account_symbol: dict[tuple[str, str], list[ActiveOrderSnapshot]] = {}
    stop_orders_by_symbol: dict[str, list[ActiveOrderSnapshot]] = {}
    for order in active_orders:
        if not _is_protective_stop_order(order, required_tag_prefix=required_tag_prefix):
            continue
        symbol = _normalize_symbol(order.symbol)
        if not symbol:
            continue
        account_key = _normalize_account_key(order.account)
        stop_orders_by_account_symbol.setdefault((account_key, symbol), []).append(order)
        stop_orders_by_symbol.setdefault(symbol, []).append(order)

    gaps: list[DetachedProtectionGap] = []
    covered_count = 0
    inspected_count = 0
    for position in positions:
        symbol = _normalize_symbol(position.symbol)
        if not symbol:
            continue
        position_qty = float(position.qty or 0.0)
        if position_qty <= 1e-9:
            continue

        account = _normalize_account(position.account)
        tag = tag_for_position(account, symbol) if tag_for_position else None
        if tag_for_position and not _tag_matches_required_prefix(
            tag,
            required_tag_prefix=required_tag_prefix,
        ):
            continue

        inspected_count += 1
        account_key = _normalize_account_key(account)
        stop_orders = list(stop_orders_by_account_symbol.get((account_key, symbol), []))
        if not stop_orders and account_key:
            stop_orders = list(stop_orders_by_symbol.get(symbol, []))

        protected_qty = sum(_remaining_qty(order) for order in stop_orders)
        uncovered_qty = max(position_qty - protected_qty, 0.0)
        if uncovered_qty <= 1e-9:
            covered_count += 1
            continue

        stop_order_ids = sorted(
            {
                int(order.order_id)
                for order in stop_orders
                if order.order_id is not None and int(order.order_id) > 0
            }
        )
        gaps.append(
            DetachedProtectionGap(
                account=account,
                symbol=symbol,
                client_tag=tag,
                position_qty=position_qty,
                protected_qty=protected_qty,
                uncovered_qty=uncovered_qty,
                stop_order_ids=stop_order_ids,
                stop_order_count=len(stop_orders),
            )
        )

    return DetachedProtectionReconciliationReport(
        active_order_count=len(active_orders),
        position_count=len(positions),
        inspected_position_count=inspected_count,
        covered_position_count=covered_count,
        gap_count=len(gaps),
        gaps=gaps,
    )


def _is_protective_stop_order(
    order: ActiveOrderSnapshot,
    *,
    required_tag_prefix: Optional[str],
) -> bool:
    if _normalize_side(order.side) != "SELL":
        return False
    if not _is_active_status(order.status, order.remaining_qty):
        return False
    if _normalize_order_type(order.order_type) not in _PROTECTIVE_STOP_ORDER_TYPES:
        return False
    if not _tag_matches_required_prefix(order.client_tag, required_tag_prefix=required_tag_prefix):
        return False
    return _remaining_qty(order) > 1e-9


def _is_active_status(status: Optional[str], remaining_qty: Optional[float]) -> bool:
    if remaining_qty is not None and float(remaining_qty) <= 1e-9:
        return False
    if not status:
        return True
    normalized = str(status).strip().lower().replace(" ", "")
    return normalized not in _INACTIVE_ORDER_STATUSES


def _remaining_qty(order: ActiveOrderSnapshot) -> float:
    if order.remaining_qty is not None:
        return max(float(order.remaining_qty), 0.0)
    if order.qty is not None and order.filled_qty is not None:
        return max(float(order.qty) - float(order.filled_qty), 0.0)
    if order.qty is not None:
        return max(float(order.qty), 0.0)
    return 0.0


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_side(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_account(value: Optional[str]) -> Optional[str]:
    normalized = _normalize_account_key(value)
    if not normalized:
        return None
    return normalized


def _normalize_account_key(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip().rstrip(".")


def _normalize_order_type(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    normalized = "".join(ch for ch in text if ch.isalnum())
    return normalized or None


def _tag_matches_required_prefix(
    client_tag: Optional[str],
    *,
    required_tag_prefix: Optional[str],
) -> bool:
    if required_tag_prefix is None:
        return True
    prefix = str(required_tag_prefix).strip().lower()
    if not prefix:
        return True
    if not client_tag:
        return False
    return str(client_tag).strip().lower().startswith(prefix)

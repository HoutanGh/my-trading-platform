from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.positions.models import PositionSnapshot

_INACTIVE_ORDER_STATUSES = {"inactive", "cancelled", "apicancelled", "filled"}
_PROTECTIVE_STOP_ORDER_TYPES = {"STP", "STOP", "STPLMT", "STOPLIMIT"}
_TAKE_PROFIT_ORDER_TYPES = {"LMT", "LIMIT"}


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


@dataclass(frozen=True)
class DetachedSessionRestored:
    account: Optional[str]
    symbol: str
    client_tag: Optional[str]
    execution_mode: str
    state: str
    reason: str
    position_qty: float
    protected_qty: float
    uncovered_qty: float
    active_take_profit_order_ids: list[int]
    active_stop_order_ids: list[int]
    primary_stop_order_id: Optional[int]


@dataclass(frozen=True)
class DetachedSessionRestoreReport:
    active_order_count: int
    position_count: int
    inspected_position_count: int
    restored_count: int
    protected_count: int
    unprotected_count: int
    sessions: list[DetachedSessionRestored]


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


def reconstruct_detached_sessions(
    *,
    positions: Sequence[PositionSnapshot],
    active_orders: Sequence[ActiveOrderSnapshot],
    tag_for_position: Optional[Callable[[Optional[str], str], Optional[str]]] = None,
    take_profit_count_for_position: Optional[Callable[[Optional[str], str], Optional[int]]] = None,
    required_tag_prefix: Optional[str] = "breakout:",
) -> DetachedSessionRestoreReport:
    orders_by_account_symbol_tag: dict[tuple[str, str, str], list[ActiveOrderSnapshot]] = {}
    orders_by_symbol_tag: dict[tuple[str, str], list[ActiveOrderSnapshot]] = {}
    for order in active_orders:
        if _normalize_side(order.side) != "SELL":
            continue
        if not _is_active_status(order.status, order.remaining_qty):
            continue
        symbol = _normalize_symbol(order.symbol)
        if not symbol:
            continue
        client_tag = (order.client_tag or "").strip()
        if not _tag_matches_required_prefix(client_tag, required_tag_prefix=required_tag_prefix):
            continue
        tag = client_tag
        account_key = _normalize_account_key(order.account)
        orders_by_account_symbol_tag.setdefault((account_key, symbol, tag), []).append(order)
        orders_by_symbol_tag.setdefault((symbol, tag), []).append(order)

    sessions: list[DetachedSessionRestored] = []
    inspected_count = 0
    protected_count = 0
    unprotected_count = 0
    for position in positions:
        symbol = _normalize_symbol(position.symbol)
        if not symbol:
            continue
        position_qty = float(position.qty or 0.0)
        if position_qty <= 1e-9:
            continue

        account = _normalize_account(position.account)
        tag = tag_for_position(account, symbol) if tag_for_position else None
        if not _tag_matches_required_prefix(tag, required_tag_prefix=required_tag_prefix):
            continue
        if tag is None:
            continue
        inspected_count += 1

        account_key = _normalize_account_key(account)
        orders = list(orders_by_account_symbol_tag.get((account_key, symbol, tag), []))
        if not orders and account_key:
            orders = list(orders_by_symbol_tag.get((symbol, tag), []))

        stop_orders = [order for order in orders if _is_stop_order(order)]
        tp_orders = [order for order in orders if _is_take_profit_order(order)]
        protected_qty = sum(_remaining_qty(order) for order in stop_orders)
        uncovered_qty = max(position_qty - protected_qty, 0.0)

        active_tp_order_ids = sorted(
            {
                int(order.order_id)
                for order in tp_orders
                if order.order_id is not None and int(order.order_id) > 0
            }
        )
        active_stop_order_ids = sorted(
            {
                int(order.order_id)
                for order in stop_orders
                if order.order_id is not None and int(order.order_id) > 0
            }
        )
        expected_tp_count = (
            take_profit_count_for_position(account, symbol)
            if take_profit_count_for_position is not None
            else None
        )
        execution_mode = _infer_execution_mode(
            expected_tp_count=expected_tp_count,
            active_tp_count=len(active_tp_order_ids),
        )
        if active_stop_order_ids and uncovered_qty <= 1e-9:
            state = "protected"
            reason = "reconnect_restored"
            protected_count += 1
        elif not active_stop_order_ids:
            state = "unprotected"
            reason = "reconnect_missing_stop_orders"
            unprotected_count += 1
        else:
            state = "unprotected"
            reason = "reconnect_insufficient_stop_qty"
            unprotected_count += 1
        sessions.append(
            DetachedSessionRestored(
                account=account,
                symbol=symbol,
                client_tag=tag,
                execution_mode=execution_mode,
                state=state,
                reason=reason,
                position_qty=position_qty,
                protected_qty=protected_qty,
                uncovered_qty=uncovered_qty,
                active_take_profit_order_ids=active_tp_order_ids,
                active_stop_order_ids=active_stop_order_ids,
                primary_stop_order_id=active_stop_order_ids[0] if active_stop_order_ids else None,
            )
        )

    return DetachedSessionRestoreReport(
        active_order_count=len(active_orders),
        position_count=len(positions),
        inspected_position_count=inspected_count,
        restored_count=len(sessions),
        protected_count=protected_count,
        unprotected_count=unprotected_count,
        sessions=sessions,
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


def _is_stop_order(order: ActiveOrderSnapshot) -> bool:
    return _normalize_order_type(order.order_type) in _PROTECTIVE_STOP_ORDER_TYPES


def _is_take_profit_order(order: ActiveOrderSnapshot) -> bool:
    return _normalize_order_type(order.order_type) in _TAKE_PROFIT_ORDER_TYPES


def _infer_execution_mode(*, expected_tp_count: Optional[int], active_tp_count: int) -> str:
    if expected_tp_count == 2:
        return "detached70"
    if expected_tp_count == 3:
        return "detached"
    # TODO: when reconnect metadata is persisted per detached session, use that source directly.
    # Active TP count can be ambiguous after partial exits.
    if active_tp_count == 2:
        return "detached70"
    return "detached"


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

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from ib_insync import IB, Trade

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.active_orders.ports import ActiveOrdersPort


class IBKRActiveOrdersPort(ActiveOrdersPort):
    def __init__(self, connection: IBKRConnection) -> None:
        self._connection = connection
        self._ib: IB = connection.ib

    async def list_active_orders(
        self,
        *,
        account: Optional[str] = None,
        scope: str = "client",
    ) -> list[ActiveOrderSnapshot]:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        normalized_scope = (scope or "client").strip().lower().replace("-", "_")
        if normalized_scope not in {"client", "all_clients"}:
            raise ValueError("scope must be 'client' or 'all_clients'")

        timeout = max(self._connection.config.timeout, 1.0)
        trades = await _fetch_open_trades(self._ib, scope=normalized_scope, timeout=timeout)

        snapshots: list[ActiveOrderSnapshot] = []
        for trade in trades:
            snapshot = _to_snapshot(trade)
            if snapshot is None:
                continue
            if not _is_active(snapshot.status, snapshot.remaining_qty):
                continue
            if account and not _account_matches(snapshot.account, account):
                continue
            snapshots.append(snapshot)

        snapshots.sort(
            key=lambda item: (
                item.account or "",
                item.symbol or "",
                item.order_id if item.order_id is not None else -1,
            )
        )
        return snapshots


async def _fetch_open_trades(ib: IB, *, scope: str, timeout: float) -> list[Trade]:
    if scope == "all_clients":
        trades = await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=timeout)
        return _to_trade_list(trades)
    await asyncio.wait_for(ib.reqOpenOrdersAsync(), timeout=timeout)
    return list(ib.openTrades())


def _to_trade_list(value: object) -> list[Trade]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Trade)]
    try:
        return [item for item in value if isinstance(item, Trade)]  # type: ignore[arg-type]
    except TypeError:
        return []


def _to_snapshot(trade: Trade) -> Optional[ActiveOrderSnapshot]:
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    if order is None or status is None:
        return None

    symbol = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None) or ""
    sec_type = getattr(contract, "secType", None) or ""
    exchange = getattr(contract, "exchange", None) or ""
    currency = getattr(contract, "currency", None) or ""

    order_id = _maybe_int(getattr(order, "orderId", None))
    perm_id = _maybe_int(getattr(order, "permId", None))
    parent_order_id = _maybe_int(getattr(order, "parentId", None))
    client_id = _maybe_int(getattr(order, "clientId", None))
    account = _normalize_account(getattr(order, "account", None))

    side = _normalize_text(getattr(order, "action", None), upper=True)
    order_type = _normalize_text(getattr(order, "orderType", None), upper=True)
    qty = _maybe_float(getattr(order, "totalQuantity", None))
    filled_qty = _maybe_float(getattr(status, "filled", None))
    remaining_qty = _maybe_float(getattr(status, "remaining", None))
    limit_price = _maybe_float(getattr(order, "lmtPrice", None))
    stop_price = _maybe_float(getattr(order, "auxPrice", None))
    status_text = _normalize_text(getattr(status, "status", None))
    tif = _normalize_text(getattr(order, "tif", None), upper=True)
    outside_rth = _maybe_bool(getattr(order, "outsideRth", None))
    client_tag = _normalize_text(getattr(order, "orderRef", None))

    return ActiveOrderSnapshot(
        order_id=order_id,
        perm_id=perm_id,
        parent_order_id=parent_order_id,
        client_id=client_id,
        account=account,
        symbol=str(symbol),
        sec_type=str(sec_type),
        exchange=str(exchange),
        currency=str(currency),
        side=side,
        order_type=order_type,
        qty=qty,
        filled_qty=filled_qty,
        remaining_qty=remaining_qty,
        limit_price=limit_price,
        stop_price=stop_price,
        status=status_text,
        tif=tif,
        outside_rth=outside_rth,
        client_tag=client_tag,
        updated_at=_last_update_at(trade),
    )


def _is_active(status: Optional[str], remaining_qty: Optional[float]) -> bool:
    if remaining_qty is not None and remaining_qty <= 0:
        return False
    if status is None:
        return True
    normalized = status.strip().lower().replace(" ", "")
    if normalized in {"cancelled", "filled", "inactive", "apicancelled"}:
        return False
    return True


def _last_update_at(trade: Trade) -> Optional[datetime]:
    entries = getattr(trade, "log", None)
    if not entries:
        return None
    for entry in reversed(entries):
        timestamp = getattr(entry, "time", None)
        if isinstance(timestamp, datetime):
            return timestamp
    return None


def _normalize_text(value: object, *, upper: bool = False) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper() if upper else text


def _normalize_account(value: object) -> Optional[str]:
    text = _normalize_text(value)
    if text is None:
        return None
    return text.rstrip(".")


def _account_matches(item_account: Optional[str], account_hint: Optional[str]) -> bool:
    if account_hint is None:
        return True
    return _normalize_account(item_account) == _normalize_account(account_hint)


def _maybe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_bool(value: object) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)

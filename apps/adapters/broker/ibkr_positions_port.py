from __future__ import annotations

import asyncio
from typing import Optional

from ib_insync import IB

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.positions.models import PositionSnapshot
from apps.core.positions.ports import PositionsPort


class IBKRPositionsPort(PositionsPort):
    def __init__(self, connection: IBKRConnection) -> None:
        self._connection = connection
        self._ib: IB = connection.ib

    async def list_positions(self, account: Optional[str] = None) -> list[PositionSnapshot]:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        accounts = [account] if account else [acct for acct in self._ib.managedAccounts() if acct]
        if not accounts:
            return []

        snapshots: list[PositionSnapshot] = []
        fallback_positions: Optional[list[object]] = None
        timeout = max(self._connection.config.timeout, 1.0)
        for acct in accounts:
            try:
                portfolio = await _refresh_account_portfolio(self._ib, acct, timeout=timeout)
                for item in portfolio:
                    snapshot = _to_snapshot(item, acct)
                    if snapshot:
                        snapshots.append(snapshot)
            except asyncio.TimeoutError as exc:
                fallback_positions = fallback_positions or await _fetch_positions(self._ib, timeout=timeout)
                fallback = [
                    item
                    for item in fallback_positions
                    if getattr(item, "account", None) in {acct, None}
                ]
                if not fallback:
                    raise RuntimeError(
                        "Timed out waiting for IBKR account updates and no positions were returned. "
                        "Verify the account id and IB API account data settings, or increase IB_TIMEOUT."
                    ) from exc
                print(
                    f"Warning: account updates timed out for {acct}; "
                    "falling back to reqPositions (market/PnL fields may be blank)."
                )
                for item in fallback:
                    snapshot = _to_position_snapshot(item, acct)
                    if snapshot:
                        snapshots.append(snapshot)
        return snapshots


async def _refresh_account_portfolio(ib: IB, account: str, *, timeout: float) -> list[object]:
    portfolio: list[object] = []
    try:
        await asyncio.wait_for(ib.reqAccountUpdatesAsync(account), timeout=timeout)
        portfolio = [
            item
            for item in ib.portfolio()
            if getattr(item, "account", None) in {account, None}
        ]
    finally:
        try:
            ib.reqAccountUpdates(False, account)
        except Exception:
            pass
    return portfolio


async def _fetch_positions(ib: IB, *, timeout: float) -> list[object]:
    return await asyncio.wait_for(ib.reqPositionsAsync(), timeout=timeout)


def _to_snapshot(item: object, account_hint: str) -> Optional[PositionSnapshot]:
    contract = getattr(item, "contract", None)
    if contract is None:
        return None
    symbol = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None) or ""
    sec_type = getattr(contract, "secType", None) or ""
    exchange = getattr(contract, "exchange", None) or ""
    currency = getattr(contract, "currency", None) or ""
    con_id = _maybe_int(getattr(contract, "conId", None))
    account = getattr(item, "account", None) or account_hint

    return PositionSnapshot(
        account=str(account),
        symbol=str(symbol),
        sec_type=str(sec_type),
        exchange=str(exchange),
        currency=str(currency),
        qty=_maybe_float(getattr(item, "position", None)) or 0.0,
        avg_cost=_maybe_float(getattr(item, "averageCost", None)),
        market_price=_maybe_float(getattr(item, "marketPrice", None)),
        market_value=_maybe_float(getattr(item, "marketValue", None)),
        unrealized_pnl=_maybe_float(getattr(item, "unrealizedPNL", None)),
        realized_pnl=_maybe_float(getattr(item, "realizedPNL", None)),
        con_id=con_id,
    )


def _to_position_snapshot(item: object, account_hint: str) -> Optional[PositionSnapshot]:
    contract = getattr(item, "contract", None)
    if contract is None:
        return None
    symbol = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None) or ""
    sec_type = getattr(contract, "secType", None) or ""
    exchange = getattr(contract, "exchange", None) or ""
    currency = getattr(contract, "currency", None) or ""
    con_id = _maybe_int(getattr(contract, "conId", None))
    account = getattr(item, "account", None) or account_hint

    return PositionSnapshot(
        account=str(account),
        symbol=str(symbol),
        sec_type=str(sec_type),
        exchange=str(exchange),
        currency=str(currency),
        qty=_maybe_float(getattr(item, "position", None)) or 0.0,
        avg_cost=_maybe_float(getattr(item, "avgCost", None)),
        con_id=con_id,
    )


def _maybe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

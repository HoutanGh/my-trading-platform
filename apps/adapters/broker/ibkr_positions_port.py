from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from typing import Optional

from ib_insync import IB

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.positions.models import PositionSnapshot
from apps.core.positions.ports import PositionsPort

_IB_UNSET_DOUBLE = 1.7976931348623157e308


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
        positions_timeout = max(self._connection.config.timeout, 1.0)
        account_updates_timeout = 0.5
        explicit_account = account is not None
        for acct in accounts:
            try:
                portfolio = await _refresh_account_portfolio(
                    self._ib,
                    acct,
                    timeout=account_updates_timeout,
                )
                for item in portfolio:
                    snapshot = _to_snapshot(item, acct)
                    if snapshot:
                        snapshots.append(snapshot)
            except asyncio.TimeoutError as exc:
                try:
                    fallback_positions = fallback_positions or await _fetch_positions(
                        self._ib,
                        timeout=positions_timeout,
                    )
                except asyncio.TimeoutError:
                    message = (
                        "Timed out waiting for IBKR positions snapshot. "
                        "Verify the account id and IB API account data settings, or increase IB_TIMEOUT."
                    )
                    print(f"Warning: {message}")
                    continue
                fallback = [
                    item
                    for item in fallback_positions
                    if _account_matches(getattr(item, "account", None), acct)
                ]
                if not fallback:
                    message = (
                        "Timed out waiting for IBKR account updates and no positions were returned. "
                        "Verify the account id and IB API account data settings, or increase IB_TIMEOUT."
                    )
                    print(f"Warning: {message}")
                    continue
                print(
                    f"Warning: account updates timed out for {acct}; "
                    "falling back to reqPositions; enriching PnL via reqPnLSingle when available."
                )
                fallback_snapshots: list[PositionSnapshot] = []
                for item in fallback:
                    snapshot = _to_position_snapshot(item, acct)
                    if snapshot:
                        fallback_snapshots.append(snapshot)
                if fallback_snapshots:
                    fallback_snapshots = await _enrich_with_single_position_pnl(
                        self._ib,
                        fallback_snapshots,
                        account=acct,
                        wait_seconds=min(max(self._connection.config.timeout * 0.2, 0.25), 1.0),
                    )
                    snapshots.extend(fallback_snapshots)
        return snapshots


async def _refresh_account_portfolio(ib: IB, account: str, *, timeout: float) -> list[object]:
    portfolio: list[object] = []
    try:
        await asyncio.wait_for(ib.reqAccountUpdatesAsync(account), timeout=timeout)
        portfolio = [
            item
            for item in ib.portfolio()
            if _account_matches(getattr(item, "account", None), account)
        ]
    finally:
        try:
            ib.reqAccountUpdates(False, account)
        except Exception:
            pass
    return portfolio


async def _fetch_positions(ib: IB, *, timeout: float) -> list[object]:
    return await asyncio.wait_for(ib.reqPositionsAsync(), timeout=timeout)


async def _enrich_with_single_position_pnl(
    ib: IB,
    snapshots: list[PositionSnapshot],
    *,
    account: str,
    wait_seconds: float,
) -> list[PositionSnapshot]:
    updated = list(snapshots)
    subscriptions: list[tuple[int, int, object]] = []
    for idx, snapshot in enumerate(updated):
        con_id = snapshot.con_id
        if con_id is None or con_id <= 0:
            continue
        if snapshot.realized_pnl is not None and snapshot.unrealized_pnl is not None:
            continue
        try:
            pnl = ib.reqPnLSingle(account, "", con_id)
        except Exception:
            continue
        subscriptions.append((idx, con_id, pnl))

    if not subscriptions:
        return updated

    try:
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        for idx, _con_id, pnl in subscriptions:
            realized = _maybe_pnl_float(getattr(pnl, "realizedPnL", None))
            unrealized = _maybe_pnl_float(getattr(pnl, "unrealizedPnL", None))
            if realized is None and unrealized is None:
                continue
            current = updated[idx]
            updated[idx] = replace(
                current,
                realized_pnl=realized if realized is not None else current.realized_pnl,
                unrealized_pnl=unrealized if unrealized is not None else current.unrealized_pnl,
            )
    finally:
        for _idx, con_id, _pnl in subscriptions:
            try:
                ib.cancelPnLSingle(account, "", con_id)
            except Exception:
                continue
    return updated


def _to_snapshot(item: object, account_hint: str) -> Optional[PositionSnapshot]:
    contract = getattr(item, "contract", None)
    if contract is None:
        return None
    symbol = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None) or ""
    sec_type = getattr(contract, "secType", None) or ""
    exchange = getattr(contract, "exchange", None) or ""
    currency = getattr(contract, "currency", None) or ""
    con_id = _maybe_int(getattr(contract, "conId", None))
    account = _normalize_account(getattr(item, "account", None)) or _normalize_account(account_hint) or account_hint

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
        unrealized_pnl=_maybe_pnl_float(getattr(item, "unrealizedPNL", None)),
        realized_pnl=_maybe_pnl_float(getattr(item, "realizedPNL", None)),
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
    account = _normalize_account(getattr(item, "account", None)) or _normalize_account(account_hint) or account_hint

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


def _maybe_pnl_float(value: object) -> Optional[float]:
    as_float = _maybe_float(value)
    if as_float is None:
        return None
    if not math.isfinite(as_float):
        return None
    # IBKR uses UNSET_DOUBLE as a sentinel for "value unavailable".
    if as_float == _IB_UNSET_DOUBLE:
        return None
    return as_float


def _maybe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_account(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized.rstrip(".")


def _account_matches(item_account: Optional[str], account_hint: Optional[str]) -> bool:
    if item_account is None:
        return True
    if account_hint is None:
        return True
    return _normalize_account(item_account) == _normalize_account(account_hint)

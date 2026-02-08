from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from ib_insync import IB, Stock, Ticker

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.market_data.models import Quote
from apps.core.market_data.ports import QuotePort


class IBKRQuoteSnapshot(QuotePort):
    def __init__(self, connection: IBKRConnection, *, timeout: float | None = None) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._timeout = timeout

    async def get_quote(self, symbol: str, *, timeout: float | None = None) -> Quote:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        contract = Stock(symbol.upper(), "SMART", "USD")
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        qualified = contracts[0]

        timeout_value = self._timeout if timeout is None else timeout

        if hasattr(self._ib, "reqTickersAsync"):
            ticker = await _snapshot_with_req_tickers(self._ib, qualified, timeout_value)
        else:
            ticker = await _snapshot_with_req_mkt_data(self._ib, qualified, timeout_value)

        bid = _maybe_price(getattr(ticker, "bid", None))
        ask = _maybe_price(getattr(ticker, "ask", None))
        timestamp = _normalize_timestamp(getattr(ticker, "time", None))
        return Quote(timestamp=timestamp, bid=bid, ask=ask)


async def _snapshot_with_req_tickers(
    ib: IB,
    contract: Stock,
    timeout_value: float | None,
) -> Ticker:
    if timeout_value and timeout_value > 0:
        tickers = await asyncio.wait_for(ib.reqTickersAsync(contract), timeout=timeout_value)
    else:
        tickers = await ib.reqTickersAsync(contract)
    if not tickers:
        raise RuntimeError("IBKR did not return a ticker snapshot")
    return tickers[0]


async def _snapshot_with_req_mkt_data(
    ib: IB,
    contract: Stock,
    timeout_value: float | None,
) -> Ticker:
    ticker = ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
    deadline = time.time() + (timeout_value if timeout_value and timeout_value > 0 else 2.0)
    while time.time() < deadline:
        if _maybe_price(getattr(ticker, "ask", None)) is not None or _maybe_price(
            getattr(ticker, "bid", None)
        ) is not None:
            break
        await asyncio.sleep(0.05)
    ib.cancelMktData(contract)
    return ticker


def _maybe_price(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price != price or price <= 0:
        return None
    return price


def _normalize_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc)

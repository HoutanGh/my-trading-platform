from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from apps.adapters.broker._ib_client import IB, Stock
from apps.adapters.broker._ib_compat import req_tickers_snapshot

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
        ticker = await req_tickers_snapshot(self._ib, qualified, timeout=timeout_value)

        bid = _maybe_price(getattr(ticker, "bid", None))
        ask = _maybe_price(getattr(ticker, "ask", None))
        timestamp = _normalize_timestamp(getattr(ticker, "time", None))
        return Quote(timestamp=timestamp, bid=bid, ask=ask)


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

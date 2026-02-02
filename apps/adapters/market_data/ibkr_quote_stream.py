from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ib_insync import IB, Stock, Ticker

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.market_data.models import Quote
from apps.core.market_data.ports import QuoteStreamPort


@dataclass
class _StreamState:
    contract: Stock
    ticker: Ticker
    ref_count: int


class IBKRQuoteStream(QuoteStreamPort):
    def __init__(
        self,
        connection: IBKRConnection,
        *,
        max_active: int = 20,
    ) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._max_active = max_active
        self._streams: dict[str, _StreamState] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, symbol: str) -> bool:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")

        async with self._lock:
            existing = self._streams.get(symbol)
            if existing:
                existing.ref_count += 1
                return True
            if self._max_active and len(self._streams) >= self._max_active:
                return False

        contract = Stock(symbol, "SMART", "USD")
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        qualified = contracts[0]
        ticker = self._ib.reqMktData(qualified, "", snapshot=False, regulatorySnapshot=False)

        async with self._lock:
            existing = self._streams.get(symbol)
            if existing:
                existing.ref_count += 1
                self._ib.cancelMktData(qualified)
                return True
            self._streams[symbol] = _StreamState(
                contract=qualified,
                ticker=ticker,
                ref_count=1,
            )
        return True

    async def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if not symbol:
            return
        state: Optional[_StreamState] = None
        async with self._lock:
            existing = self._streams.get(symbol)
            if not existing:
                return
            existing.ref_count -= 1
            if existing.ref_count > 0:
                return
            state = self._streams.pop(symbol, None)
        if state:
            self._ib.cancelMktData(state.contract)

    def get_latest(self, symbol: str) -> Optional[Quote]:
        symbol = symbol.strip().upper()
        if not symbol:
            return None
        state = self._streams.get(symbol)
        if not state:
            return None
        ticker = state.ticker
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

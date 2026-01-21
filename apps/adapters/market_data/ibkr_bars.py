from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator

from ib_insync import IB, BarData, Stock
from ib_insync.util import parseIBDatetime

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.core.market_data.models import Bar
from apps.core.market_data.ports import BarStreamPort


class IBKRBarStream(BarStreamPort):
    def __init__(self, connection: IBKRConnection) -> None:
        self._connection = connection
        self._ib: IB = connection.ib

    async def stream_bars(
        self,
        symbol: str,
        *,
        bar_size: str = "1 min",
        use_rth: bool = False,
    ) -> AsyncIterator[Bar]:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        contract = Stock(symbol.upper(), "SMART", "USD")
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        qualified = contracts[0]

        bars = await self._ib.reqHistoricalDataAsync(
            qualified,
            endDateTime="",
            durationStr="2 D",
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=use_rth,
            keepUpToDate=True,
        )

        queue: asyncio.Queue[BarData] = asyncio.Queue()
        last_count = len(bars)

        def _on_bar(_bars, has_new_bar: bool) -> None:
            nonlocal last_count
            if not has_new_bar:
                return
            # When a new bar is appended, the previous bar is now closed.
            if last_count == 0:
                last_count = len(_bars)
                return
            start_index = max(last_count - 1, 0)
            end_index = max(len(_bars) - 1, start_index)
            for item in _bars[start_index:end_index]:
                queue.put_nowait(item)
            last_count = len(_bars)

        bars.updateEvent += _on_bar

        try:
            while True:
                ib_bar = await queue.get()
                yield _to_bar(ib_bar)
        finally:
            bars.updateEvent -= _on_bar
            try:
                self._ib.cancelHistoricalData(bars)
            except Exception:
                pass


def _to_bar(ib_bar: BarData) -> Bar:
    timestamp = ib_bar.date
    if not isinstance(timestamp, datetime):
        timestamp = parseIBDatetime(timestamp)
    return Bar(
        timestamp=timestamp,
        open=float(ib_bar.open),
        high=float(ib_bar.high),
        low=float(ib_bar.low),
        close=float(ib_bar.close),
        volume=float(ib_bar.volume) if ib_bar.volume is not None else None,
    )

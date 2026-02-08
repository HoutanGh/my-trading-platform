from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from ib_insync import IB, Stock

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.adapters.market_data._ibkr_bar_utils import (
    duration_for_window as _duration_for_window,
)
from apps.adapters.market_data._ibkr_bar_utils import (
    normalize_timestamp as _normalize_timestamp,
)
from apps.adapters.market_data._ibkr_bar_utils import to_bar as _to_bar
from apps.core.market_data.models import Bar
from apps.core.market_data.ports import BarHistoryPort


class IBKRBarHistory(BarHistoryPort):
    def __init__(
        self,
        connection: IBKRConnection,
        *,
        event_logger: Optional[Callable[[object], None]] = None,
    ) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._event_logger = event_logger

    async def fetch_bars(
        self,
        symbol: str,
        *,
        bar_size: str = "1 min",
        start: datetime | None = None,
        end: datetime | None = None,
        use_rth: bool = False,
    ) -> list[Bar]:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")
        if start is None:
            raise ValueError("start is required for historical bars")
        if end is None:
            end = datetime.now(timezone.utc)

        start_ts = _normalize_timestamp(start)
        end_ts = _normalize_timestamp(end)
        if end_ts <= start_ts:
            raise ValueError("end must be after start for historical bars")

        contract = Stock(symbol.upper(), "SMART", "USD")
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        qualified = contracts[0]

        duration = _duration_for_window(start_ts, end_ts)
        bars = await self._ib.reqHistoricalDataAsync(
            qualified,
            endDateTime=end_ts,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=use_rth,
            keepUpToDate=False,
        )

        return [_to_bar(bar) for bar in bars]

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Optional

from ib_insync import IB, BarData, Stock

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.adapters.market_data._ibkr_bar_utils import (
    bar_interval_seconds as _bar_interval_seconds,
)
from apps.adapters.market_data._ibkr_bar_utils import (
    duration_for_bar_size as _duration_for_bar_size,
)
from apps.adapters.market_data._ibkr_bar_utils import (
    normalize_timestamp as _normalize_timestamp,
)
from apps.adapters.market_data._ibkr_bar_utils import to_bar as _to_bar
from apps.core.market_data.models import Bar
from apps.core.market_data.ports import BarStreamPort
from apps.core.ops.events import BarStreamGapDetected, BarStreamLagDetected, BarStreamStarted, BarStreamStopped


class IBKRBarStream(BarStreamPort):
    def __init__(
        self,
        connection: IBKRConnection,
        *,
        event_logger: Optional[Callable[[object], None]] = None,
    ) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._event_logger = event_logger

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

        duration = _duration_for_bar_size(bar_size)
        bars = await self._ib.reqHistoricalDataAsync(
            qualified,
            endDateTime="",
            durationStr=duration,
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

        expected_interval = _bar_interval_seconds(bar_size)
        last_bar_ts: Optional[datetime] = None
        stop_reason: Optional[str] = None
        self._log_event(BarStreamStarted.now(symbol=symbol.upper(), bar_size=bar_size, use_rth=use_rth))

        try:
            while True:
                ib_bar = await queue.get()
                bar = _to_bar(ib_bar)
                if expected_interval:
                    if last_bar_ts is not None:
                        actual_interval = (_normalize_timestamp(bar.timestamp) - _normalize_timestamp(last_bar_ts)).total_seconds()
                        if actual_interval > expected_interval * 1.5:
                            self._log_event(
                                BarStreamGapDetected.now(
                                    symbol=symbol.upper(),
                                    bar_size=bar_size,
                                    use_rth=use_rth,
                                    expected_interval_seconds=expected_interval,
                                    actual_interval_seconds=actual_interval,
                                    previous_bar_timestamp=_normalize_timestamp(last_bar_ts),
                                    current_bar_timestamp=_normalize_timestamp(bar.timestamp),
                                )
                            )
                    lag_seconds = (_normalize_timestamp(datetime.now(timezone.utc)) - _normalize_timestamp(bar.timestamp)).total_seconds()
                    if lag_seconds > expected_interval * 2.5:
                        self._log_event(
                            BarStreamLagDetected.now(
                                symbol=symbol.upper(),
                                bar_size=bar_size,
                                use_rth=use_rth,
                                lag_seconds=lag_seconds,
                                bar_timestamp=_normalize_timestamp(bar.timestamp),
                            )
                        )
                last_bar_ts = bar.timestamp
                yield bar
        except asyncio.CancelledError:
            stop_reason = "cancelled"
            raise
        finally:
            bars.updateEvent -= _on_bar
            try:
                self._ib.cancelHistoricalData(bars)
            except Exception:
                pass
            self._log_event(
                BarStreamStopped.now(
                    symbol=symbol.upper(),
                    bar_size=bar_size,
                    use_rth=use_rth,
                    reason=stop_reason,
                    last_bar_timestamp=_normalize_timestamp(last_bar_ts) if last_bar_ts else None,
                )
            )

    def _log_event(self, event: object) -> None:
        if self._event_logger:
            self._event_logger(event)

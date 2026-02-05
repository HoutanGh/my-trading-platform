from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

from apps.core.market_data.models import Bar, Quote


class BarStreamPort(Protocol):
    def stream_bars(
        self,
        symbol: str,
        *,
        bar_size: str = "1 min",
        use_rth: bool = False,
    ) -> AsyncIterator[Bar]:
        """Return an async iterator of live bars for the symbol."""
        raise NotImplementedError


class BarHistoryPort(Protocol):
    async def fetch_bars(
        self,
        symbol: str,
        *,
        bar_size: str = "1 min",
        start: datetime | None = None,
        end: datetime | None = None,
        use_rth: bool = False,
    ) -> list[Bar]:
        """Return historical bars for the symbol within the requested time window."""
        raise NotImplementedError


class QuotePort(Protocol):
    async def get_quote(
        self,
        symbol: str,
        *,
        timeout: float | None = None,
    ) -> Quote:
        """Return a snapshot quote for the symbol."""
        raise NotImplementedError


class QuoteStreamPort(Protocol):
    async def subscribe(self, symbol: str) -> bool:
        """Start a streaming quote subscription for the symbol."""
        raise NotImplementedError

    async def unsubscribe(self, symbol: str) -> None:
        """Stop a streaming quote subscription for the symbol."""
        raise NotImplementedError

    def get_latest(self, symbol: str) -> Quote | None:
        """Return the latest cached quote for the symbol."""
        raise NotImplementedError

from __future__ import annotations

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


class QuotePort(Protocol):
    async def get_quote(
        self,
        symbol: str,
        *,
        timeout: float | None = None,
    ) -> Quote:
        """Return a snapshot quote for the symbol."""
        raise NotImplementedError

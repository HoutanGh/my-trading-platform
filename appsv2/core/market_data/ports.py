from __future__ import annotations

from typing import AsyncIterator, Protocol

from appsv2.core.market_data.models import Bar


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

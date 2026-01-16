from __future__ import annotations

from typing import Optional, Protocol

from apps.core.positions.models import PositionSnapshot


class PositionsPort(Protocol):
    async def list_positions(self, account: Optional[str] = None) -> list[PositionSnapshot]:
        """Return positions for the given account or all accounts when None."""
        raise NotImplementedError

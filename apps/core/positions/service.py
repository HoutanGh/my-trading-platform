from __future__ import annotations

from typing import Optional

from apps.core.positions.models import PositionSnapshot
from apps.core.positions.ports import PositionsPort


class PositionsService:
    def __init__(self, port: PositionsPort) -> None:
        self._port = port

    async def list_positions(self, account: Optional[str] = None) -> list[PositionSnapshot]:
        return await self._port.list_positions(account=account)

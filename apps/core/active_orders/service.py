from __future__ import annotations

from typing import Optional

from apps.core.active_orders.models import ActiveOrderSnapshot
from apps.core.active_orders.ports import ActiveOrdersPort


class ActiveOrdersService:
    def __init__(self, port: ActiveOrdersPort) -> None:
        self._port = port

    async def list_active_orders(
        self,
        *,
        account: Optional[str] = None,
        scope: str = "client",
    ) -> list[ActiveOrderSnapshot]:
        normalized_scope = (scope or "client").strip().lower().replace("-", "_")
        if normalized_scope not in {"client", "all_clients"}:
            raise ValueError("scope must be 'client' or 'all_clients'")
        return await self._port.list_active_orders(account=account, scope=normalized_scope)

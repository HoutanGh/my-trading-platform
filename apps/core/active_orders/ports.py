from __future__ import annotations

from typing import Optional, Protocol

from apps.core.active_orders.models import ActiveOrderSnapshot


class ActiveOrdersPort(Protocol):
    async def list_active_orders(
        self,
        *,
        account: Optional[str] = None,
        scope: str = "client",
    ) -> list[ActiveOrderSnapshot]:
        """
        Return active orders from the broker.

        scope:
            - "client": current API client only
            - "all_clients": all API clients visible to the session
        """
        raise NotImplementedError

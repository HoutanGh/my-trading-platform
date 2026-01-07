from __future__ import annotations

from typing import Protocol

from appsv2.core.orders.models import OrderAck, OrderSpec


class OrderPort(Protocol):
    async def submit_order(self, spec: OrderSpec) -> OrderAck:
        """Submit an order to the broker and return an acknowledgement."""
        raise NotImplementedError

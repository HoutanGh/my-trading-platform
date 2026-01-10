from __future__ import annotations

from typing import Awaitable, Callable, Protocol, TypeVar

from appsv2.core.orders.models import OrderAck, OrderSpec

EventT = TypeVar("EventT")
EventHandler = Callable[[EventT], Awaitable[None] | None]


class OrderPort(Protocol):
    async def submit_order(self, spec: OrderSpec) -> OrderAck:
        """Submit an order to the broker and return an acknowledgement."""
        raise NotImplementedError


class EventBus(Protocol):
    def publish(self, event: object) -> None:
        """Publish an event to subscribers."""
        raise NotImplementedError

    def subscribe(self, event_type: type[EventT], handler: EventHandler[EventT]) -> Callable[[], None]:
        """Subscribe a handler to events of a given type."""
        raise NotImplementedError

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, TypeVar

from apps.core.orders.models import (
    BracketOrderSpec,
    LadderOrderSpec,
    OrderAck,
    OrderCancelSpec,
    OrderReplaceSpec,
    OrderSpec,
)

EventT = TypeVar("EventT")
EventHandler = Callable[[EventT], Awaitable[None] | None]


class OrderPort(Protocol):
    async def prewarm_session_phase(
        self,
        *,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> None:
        """Preload broker session windows used for RTH/outside-RTH detection."""
        raise NotImplementedError

    def clear_session_phase_cache(self) -> None:
        """Clear cached broker session windows."""
        raise NotImplementedError

    async def submit_order(self, spec: OrderSpec) -> OrderAck:
        """Submit an order to the broker and return an acknowledgement."""
        raise NotImplementedError

    async def cancel_order(self, spec: OrderCancelSpec) -> OrderAck:
        """Cancel an order at the broker and return an acknowledgement."""
        raise NotImplementedError

    async def replace_order(self, spec: OrderReplaceSpec) -> OrderAck:
        """Replace an existing order at the broker and return an acknowledgement."""
        raise NotImplementedError

    async def submit_bracket_order(self, spec: BracketOrderSpec) -> OrderAck:
        """Submit a bracket order to the broker and return an acknowledgement."""
        raise NotImplementedError

    async def submit_ladder_order(self, spec: LadderOrderSpec) -> OrderAck:
        """Submit a take-profit ladder with a managed stop-loss order."""
        raise NotImplementedError


class EventBus(Protocol):
    def publish(self, event: object) -> None:
        """Publish an event to subscribers."""
        raise NotImplementedError

    def subscribe(self, event_type: type[EventT], handler: EventHandler[EventT]) -> Callable[[], None]:
        """Subscribe a handler to events of a given type."""
        raise NotImplementedError

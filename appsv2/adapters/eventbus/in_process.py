from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable, TypeVar

EventT = TypeVar("EventT")
EventHandler = Callable[[EventT], Awaitable[None] | None]


class InProcessEventBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[type, EventHandler]] = []

    def publish(self, event: object) -> None:
        if not self._subscribers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        for event_type, handler in list(self._subscribers):
            if not isinstance(event, event_type):
                continue
            if loop and loop.is_running():
                loop.call_soon(self._dispatch, handler, event)
            else:
                self._dispatch(handler, event)

    def subscribe(self, event_type: type[EventT], handler: EventHandler[EventT]) -> Callable[[], None]:
        entry = (event_type, handler)
        self._subscribers.append(entry)

        def _unsubscribe() -> None:
            if entry in self._subscribers:
                self._subscribers.remove(entry)

        return _unsubscribe

    @staticmethod
    def _dispatch(handler: EventHandler, event: object) -> None:
        try:
            result = handler(event)
        except Exception as exc:
            print(f"Event handler error: {exc}")
            return

        if inspect.isawaitable(result):
            try:
                task = asyncio.create_task(result)
            except RuntimeError:
                asyncio.run(result)
            else:
                task.add_done_callback(_swallow_task_error)


def _swallow_task_error(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as exc:
        print(f"Event handler task error: {exc}")

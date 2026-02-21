from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any, Awaitable, Optional

from apps.adapters.broker._ib_client import IB_CLIENT_BACKEND


def attach_trade_events(
    trade: object,
    *,
    on_status: Callable[..., None] | None = None,
    on_filled: Callable[..., None] | None = None,
    on_fill: Callable[..., None] | None = None,
) -> tuple[str, ...]:
    attached: list[str] = []
    if on_status is not None and _event_add(trade, "statusEvent", on_status):
        attached.append("statusEvent")
    if on_filled is not None and _event_add(trade, "filledEvent", on_filled):
        attached.append("filledEvent")
    if on_fill is not None and _event_add(trade, "fillEvent", on_fill):
        attached.append("fillEvent")
    return tuple(attached)


def attach_bar_update_event(
    bars: object,
    handler: Callable[..., None],
) -> bool:
    return _event_add(bars, "updateEvent", handler)


def detach_bar_update_event(
    bars: object,
    handler: Callable[..., None],
) -> bool:
    return _event_remove(bars, "updateEvent", handler)


async def req_tickers_snapshot(
    ib: object,
    contract: object,
    *,
    timeout: float | None = None,
    poll_interval: float = 0.05,
) -> object:
    req_tickers_async = getattr(ib, "reqTickersAsync", None)
    if callable(req_tickers_async):
        tickers = await _await_with_timeout(req_tickers_async(contract), timeout=timeout)
        if not tickers:
            raise RuntimeError("IBKR did not return a ticker snapshot")
        return tickers[0]

    req_mkt_data = getattr(ib, "reqMktData", None)
    cancel_mkt_data = getattr(ib, "cancelMktData", None)
    if not callable(req_mkt_data) or not callable(cancel_mkt_data):
        raise RuntimeError("IB client does not support ticker snapshot APIs")

    ticker = req_mkt_data(contract, "", snapshot=True, regulatorySnapshot=False)
    deadline = time.time() + (timeout if timeout and timeout > 0 else 2.0)
    while time.time() < deadline:
        if _maybe_price(getattr(ticker, "ask", None)) is not None or _maybe_price(
            getattr(ticker, "bid", None)
        ) is not None:
            break
        await asyncio.sleep(poll_interval)
    cancel_mkt_data(contract)
    return ticker


async def what_if_order(
    ib: object,
    contract: object,
    order: object,
    *,
    timeout: float | None = None,
    suppress_req_id: Callable[[int], None] | None = None,
) -> tuple[object, Optional[int]]:
    state_awaitable: Awaitable[object]
    expected_req_id: Optional[int] = None

    client = getattr(ib, "client", None)
    wrapper = getattr(ib, "wrapper", None)
    get_req_id = getattr(client, "getReqId", None) if client is not None else None
    start_req = getattr(wrapper, "startReq", None) if wrapper is not None else None
    place_order = getattr(client, "placeOrder", None) if client is not None else None
    what_if_async = getattr(ib, "whatIfOrderAsync", None)

    if callable(get_req_id) and callable(start_req) and callable(place_order):
        expected_req_id = int(get_req_id())
        if suppress_req_id is not None:
            suppress_req_id(expected_req_id)
        what_if = copy.copy(order)
        setattr(what_if, "whatIf", True)
        state_awaitable = start_req(expected_req_id, contract)
        place_order(expected_req_id, contract, what_if)
    elif callable(what_if_async):
        state_awaitable = what_if_async(contract, order)
    else:
        raise RuntimeError("IB client does not support what-if order requests")

    return await _await_with_timeout(state_awaitable, timeout=timeout), expected_req_id


def silence_ib_client_loggers(*, logger_names: Iterable[str] | None = None) -> tuple[str, ...]:
    if logger_names is None:
        names: tuple[str, ...] = tuple(dict.fromkeys((IB_CLIENT_BACKEND, "ib_async", "ib_insync")))
    else:
        names = tuple(dict.fromkeys(str(name) for name in logger_names if str(name).strip()))

    for name in names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
    return names


async def _await_with_timeout(awaitable: Awaitable[object], *, timeout: float | None) -> object:
    if timeout is not None and timeout > 0:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    return await awaitable


def _event_add(owner: object, event_name: str, handler: Callable[..., None]) -> bool:
    event = getattr(owner, event_name, None)
    if event is None:
        return False
    try:
        event += handler
        return True
    except Exception:
        return False


def _event_remove(owner: object, event_name: str, handler: Callable[..., None]) -> bool:
    event = getattr(owner, event_name, None)
    if event is None:
        return False
    try:
        event -= handler
        return True
    except Exception:
        return False


def _maybe_price(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price != price or price <= 0:
        return None
    return price


__all__ = [
    "attach_bar_update_event",
    "attach_trade_events",
    "detach_bar_update_event",
    "req_tickers_snapshot",
    "silence_ib_client_loggers",
    "what_if_order",
]

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from apps.adapters.broker._ib_client import IB, Stock

from apps.core.orders.events import (
    OrderSessionPhasePrewarmFailed,
    OrderSessionPhasePrewarmed,
    OrderSessionPhaseResolved,
)
from apps.core.orders.ports import EventBus


class SessionPhase(str, Enum):
    RTH = "RTH"
    OUTSIDE_RTH = "OUTSIDE_RTH"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class _SessionCacheEntry:
    symbol: str
    exchange: str
    currency: str
    timezone_id: str
    liquid_sessions: tuple[tuple[datetime, datetime], ...]
    trading_sessions: tuple[tuple[datetime, datetime], ...]
    expires_at: datetime


class IBKRSessionPhaseResolver:
    def __init__(self, ib: IB, *, event_bus: EventBus | None = None) -> None:
        self._ib = ib
        self._event_bus = event_bus
        self._cache: dict[str, _SessionCacheEntry] = {}
        self._lock = asyncio.Lock()

    def clear_cache(self) -> None:
        self._cache.clear()

    async def prewarm(self, *, symbol: str, exchange: str = "SMART", currency: str = "USD") -> None:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")
        contract = Stock(symbol.strip().upper(), exchange.strip().upper(), currency.strip().upper())
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        await self._prewarm_contract(contracts[0])

    async def _prewarm_contract(self, contract: Stock) -> None:
        symbol = str(getattr(contract, "symbol", "") or "").strip().upper()
        exchange = str(getattr(contract, "exchange", "") or "SMART").strip().upper()
        currency = str(getattr(contract, "currency", "") or "USD").strip().upper()
        cache_key = _cache_key(contract)
        now_utc = datetime.now(timezone.utc)
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and now_utc < cached.expires_at:
                return

        try:
            details_list = await self._ib.reqContractDetailsAsync(contract)
            if not details_list:
                raise RuntimeError("No contract details returned")
            details = details_list[0]
            liquid_sessions = tuple((session.start, session.end) for session in details.liquidSessions())
            trading_sessions = tuple((session.start, session.end) for session in details.tradingSessions())
            if not trading_sessions and liquid_sessions:
                trading_sessions = liquid_sessions
            if not trading_sessions:
                raise RuntimeError("No trading sessions available from IB contract details")

            timezone_id = str(getattr(details, "timeZoneId", "") or "UTC")
            expires_at = _next_local_midnight_utc(now_utc, sessions=trading_sessions)
            entry = _SessionCacheEntry(
                symbol=symbol,
                exchange=exchange,
                currency=currency,
                timezone_id=timezone_id,
                liquid_sessions=liquid_sessions,
                trading_sessions=trading_sessions,
                expires_at=expires_at,
            )
            async with self._lock:
                self._cache[cache_key] = entry
            if self._event_bus:
                self._event_bus.publish(
                    OrderSessionPhasePrewarmed.now(
                        symbol=symbol,
                        exchange=exchange,
                        currency=currency,
                        timezone_id=timezone_id,
                        trading_session_count=len(trading_sessions),
                        liquid_session_count=len(liquid_sessions),
                        cache_key=cache_key,
                        expires_at=expires_at,
                    )
                )
        except Exception as exc:
            if self._event_bus:
                self._event_bus.publish(
                    OrderSessionPhasePrewarmFailed.now(
                        symbol=symbol,
                        exchange=exchange,
                        currency=currency,
                        cache_key=cache_key,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
            raise

    def resolve_phase(self, contract: Stock, *, now_utc: Optional[datetime] = None) -> SessionPhase:
        now_utc = now_utc or datetime.now(timezone.utc)
        cache_key = _cache_key(contract)
        entry = self._cache.get(cache_key)
        symbol = str(getattr(contract, "symbol", "") or "").strip().upper()
        exchange = str(getattr(contract, "exchange", "") or "SMART").strip().upper()
        currency = str(getattr(contract, "currency", "") or "USD").strip().upper()
        if entry is None or now_utc >= entry.expires_at:
            if entry is not None and now_utc >= entry.expires_at:
                self._cache.pop(cache_key, None)
            phase = SessionPhase.OUTSIDE_RTH
            self._publish_resolution(
                symbol=symbol,
                exchange=exchange,
                currency=currency,
                cache_key=cache_key,
                phase=phase,
                cache_hit=False,
                fallback_used=True,
            )
            return phase

        in_liquid = _within_any_session(now_utc, entry.liquid_sessions)
        in_trading = _within_any_session(now_utc, entry.trading_sessions)
        if in_liquid:
            phase = SessionPhase.RTH
        elif in_trading:
            phase = SessionPhase.OUTSIDE_RTH
        else:
            phase = SessionPhase.CLOSED
        self._publish_resolution(
            symbol=symbol,
            exchange=exchange,
            currency=currency,
            cache_key=cache_key,
            phase=phase,
            cache_hit=True,
            fallback_used=False,
        )
        return phase

    def _publish_resolution(
        self,
        *,
        symbol: str,
        exchange: str,
        currency: str,
        cache_key: str,
        phase: SessionPhase,
        cache_hit: bool,
        fallback_used: bool,
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            OrderSessionPhaseResolved.now(
                symbol=symbol,
                exchange=exchange,
                currency=currency,
                cache_key=cache_key,
                phase=phase.value,
                cache_hit=cache_hit,
                fallback_used=fallback_used,
            )
        )


def _cache_key(contract: Stock) -> str:
    con_id = getattr(contract, "conId", None)
    con_id_text = str(con_id).strip() if con_id is not None else ""
    if con_id_text and con_id_text != "0":
        return f"conid:{con_id_text}"
    symbol = str(getattr(contract, "symbol", "") or "").strip().upper()
    exchange = str(getattr(contract, "exchange", "") or "SMART").strip().upper()
    currency = str(getattr(contract, "currency", "") or "USD").strip().upper()
    return f"{symbol}:{exchange}:{currency}"


def _within_any_session(now_utc: datetime, sessions: tuple[tuple[datetime, datetime], ...]) -> bool:
    for start, end in sessions:
        if now_utc < start.astimezone(timezone.utc):
            continue
        if now_utc < end.astimezone(timezone.utc):
            return True
    return False


def _next_local_midnight_utc(
    now_utc: datetime, *, sessions: tuple[tuple[datetime, datetime], ...]
) -> datetime:
    tz = sessions[0][0].tzinfo
    if tz is None:
        return now_utc + timedelta(hours=12)
    now_local = now_utc.astimezone(tz)
    next_midnight_local = datetime.combine(now_local.date() + timedelta(days=1), datetime.min.time(), tz)
    return next_midnight_local.astimezone(timezone.utc)

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from apps.adapters.broker._ib_client import IB, BarData, Stock
from apps.adapters.broker._ib_compat import (
    attach_bar_update_event,
    detach_bar_update_event,
)

from apps.adapters.broker.ibkr_connection import IBKRConnection
from apps.adapters.market_data._ibkr_bar_utils import (
    bar_interval_seconds as _bar_interval_seconds,
)
from apps.adapters.market_data._ibkr_bar_utils import (
    duration_for_bar_size as _duration_for_bar_size,
)
from apps.adapters.market_data._ibkr_bar_utils import (
    normalize_timestamp as _normalize_timestamp,
)
from apps.adapters.market_data._ibkr_bar_utils import to_bar as _to_bar
from apps.core.market_data.models import Bar
from apps.core.market_data.ports import BarStreamPort
from apps.core.ops.events import (
    BarStreamCompetingSessionBlocked,
    BarStreamCompetingSessionCleared,
    BarStreamGapDetected,
    BarStreamLagDetected,
    BarStreamRecovered,
    BarStreamRecoveryFailed,
    BarStreamRecoveryScanScheduled,
    BarStreamRecoveryStarted,
    BarStreamStarted,
    BarStreamStalled,
    BarStreamStopped,
)

_COMPETING_SESSION_CODE = 10197
_RECONNECT_RECOVERY_CODES = {2104, 2106, 1102}


@dataclass
class _StreamHealth:
    stream_id: int
    symbol: str
    bar_size: str
    use_rth: bool
    contract: Stock
    duration: str
    queue: asyncio.Queue[BarData]
    bars: Any
    on_bar: Callable[[Any, bool], None]
    last_count: int
    expected_interval_seconds: Optional[float]
    stall_timeout_seconds: float
    status: str = "starting"
    last_bar_received_monotonic: Optional[float] = None
    last_bar_timestamp: Optional[datetime] = None
    last_emitted_bar_timestamp: Optional[datetime] = None
    stalled_since_monotonic: Optional[float] = None
    blocked_message: Optional[str] = None
    recover_attempt: int = 0
    next_retry_monotonic: float = 0.0
    closed: bool = False


class IBKRBarStream(BarStreamPort):
    def __init__(
        self,
        connection: IBKRConnection,
        *,
        event_logger: Optional[Callable[[object], None]] = None,
    ) -> None:
        self._connection = connection
        self._ib: IB = connection.ib
        self._event_logger = event_logger
        self._health_poll_seconds = _env_float("APPS_BAR_HEALTH_POLL_SECS", default=1.0, minimum=0.1)
        self._stall_multiplier = _env_float("APPS_BAR_STALL_MULTIPLIER", default=2.5, minimum=1.0)
        self._stall_floor_seconds = _env_float("APPS_BAR_STALL_FLOOR_SECS", default=5.0, minimum=1.0)
        self._self_heal_enabled = _env_bool("APPS_BAR_SELF_HEAL_ENABLED", default=False)
        self._recovery_cooldown_seconds = _env_float("APPS_BAR_RECOVERY_COOLDOWN_SECS", default=5.0, minimum=0.5)
        self._recovery_max_attempts = _env_int("APPS_BAR_RECOVERY_MAX_ATTEMPTS", default=5, minimum=1)
        self._recovery_max_concurrency = _env_int("APPS_BAR_RECOVERY_MAX_CONCURRENCY", default=1, minimum=1)
        self._stall_burst_count = _env_int("APPS_BAR_RECOVERY_STALL_BURST_COUNT", default=2, minimum=2)
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._health_lock = asyncio.Lock()
        self._next_stream_id = 1
        self._active_streams: dict[int, _StreamHealth] = {}
        self._health_task: Optional[asyncio.Task] = None
        self._recovery_queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._recovery_queued_groups: set[tuple[str, bool]] = set()
        self._recovery_in_progress_groups: set[tuple[str, bool]] = set()
        self._recovery_tasks: list[asyncio.Task] = []
        subscribe = getattr(connection, "subscribe_gateway_messages", None)
        if callable(subscribe):
            subscribe(self._on_gateway_message)

    async def stream_bars(
        self,
        symbol: str,
        *,
        bar_size: str = "1 min",
        use_rth: bool = False,
    ) -> AsyncIterator[Bar]:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR is not connected")

        contract = Stock(symbol.upper(), "SMART", "USD")
        contracts = await self._ib.qualifyContractsAsync(contract)
        if not contracts:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        qualified = contracts[0]

        duration = _duration_for_bar_size(bar_size)
        bars = await self._request_live_bars(
            contract=qualified,
            duration=duration,
            bar_size=bar_size,
            use_rth=use_rth,
        )

        queue: asyncio.Queue[BarData] = asyncio.Queue()
        stream_id = self._next_stream_id
        self._next_stream_id += 1

        expected_interval = _bar_interval_seconds(bar_size)
        # IB keepUpToDate historical streams can arrive on a ~5s cadence even when
        # requesting sub-5s bars; treat that as the floor for stall detection.
        stall_expected_interval = expected_interval
        normalized_bar_size = bar_size.strip().lower()
        if stall_expected_interval is not None and "sec" in normalized_bar_size:
            stall_expected_interval = max(stall_expected_interval, 5.0)
        timeout_seconds = _stall_timeout_seconds(
            expected_interval_seconds=stall_expected_interval,
            multiplier=self._stall_multiplier,
            floor_seconds=self._stall_floor_seconds,
        )
        stream = _StreamHealth(
            stream_id=stream_id,
            symbol=symbol.upper(),
            bar_size=bar_size,
            use_rth=use_rth,
            contract=qualified,
            duration=duration,
            queue=queue,
            bars=bars,
            on_bar=lambda _bars, _has_new_bar: None,
            last_count=len(bars),
            expected_interval_seconds=expected_interval,
            stall_timeout_seconds=timeout_seconds,
        )

        def _on_bar(_bars: Any, has_new_bar: bool) -> None:
            if not has_new_bar:
                return
            # When a new bar is appended, the previous bar is now closed.
            if stream.last_count == 0:
                stream.last_count = len(_bars)
                return
            start_index = max(stream.last_count - 1, 0)
            end_index = max(len(_bars) - 1, start_index)
            for item in _bars[start_index:end_index]:
                queue.put_nowait(item)
            stream.last_count = len(_bars)

        stream.on_bar = _on_bar
        attach_bar_update_event(bars, _on_bar)

        await self._register_stream(stream)
        last_bar_ts: Optional[datetime] = None
        stop_reason: Optional[str] = None
        self._log_event(BarStreamStarted.now(symbol=symbol.upper(), bar_size=bar_size, use_rth=use_rth))

        try:
            while True:
                ib_bar = await queue.get()
                bar = _to_bar(ib_bar)
                bar_ts = _normalize_timestamp(bar.timestamp)
                if stream.last_emitted_bar_timestamp is not None:
                    if bar_ts <= _normalize_timestamp(stream.last_emitted_bar_timestamp):
                        continue
                stream.last_emitted_bar_timestamp = bar_ts
                self._mark_stream_bar(stream_id, bar.timestamp)
                if expected_interval:
                    if last_bar_ts is not None:
                        actual_interval = (_normalize_timestamp(bar.timestamp) - _normalize_timestamp(last_bar_ts)).total_seconds()
                        if actual_interval > expected_interval * 1.5:
                            self._log_event(
                                BarStreamGapDetected.now(
                                    symbol=symbol.upper(),
                                    bar_size=bar_size,
                                    use_rth=use_rth,
                                    expected_interval_seconds=expected_interval,
                                    actual_interval_seconds=actual_interval,
                                    previous_bar_timestamp=_normalize_timestamp(last_bar_ts),
                                    current_bar_timestamp=_normalize_timestamp(bar.timestamp),
                                )
                            )
                    lag_seconds = (_normalize_timestamp(datetime.now(timezone.utc)) - _normalize_timestamp(bar.timestamp)).total_seconds()
                    if lag_seconds > expected_interval * 2.5:
                        self._log_event(
                            BarStreamLagDetected.now(
                                symbol=symbol.upper(),
                                bar_size=bar_size,
                                use_rth=use_rth,
                                lag_seconds=lag_seconds,
                                bar_timestamp=_normalize_timestamp(bar.timestamp),
                            )
                        )
                last_bar_ts = bar.timestamp
                yield bar
        except asyncio.CancelledError:
            stop_reason = "cancelled"
            raise
        finally:
            stream.closed = True
            detach_bar_update_event(stream.bars, stream.on_bar)
            try:
                self._ib.cancelHistoricalData(stream.bars)
            except Exception:
                pass
            await self._unregister_stream(stream_id)
            self._log_event(
                BarStreamStopped.now(
                    symbol=symbol.upper(),
                    bar_size=bar_size,
                    use_rth=use_rth,
                    reason=stop_reason,
                    last_bar_timestamp=_normalize_timestamp(last_bar_ts) if last_bar_ts else None,
                )
            )

    def get_stream_health(
        self,
        symbol: str,
        *,
        bar_size: str = "1 min",
        use_rth: bool = False,
    ) -> Optional[dict[str, object]]:
        symbol_key = symbol.strip().upper()
        if not symbol_key:
            return None
        matches = [
            stream
            for stream in self._active_streams.values()
            if stream.symbol == symbol_key and stream.bar_size == bar_size and stream.use_rth == use_rth
        ]
        if not matches:
            return None
        worst = max(matches, key=lambda item: _status_rank(item.status))
        silence_seconds = None
        if worst.last_bar_received_monotonic is not None:
            silence_seconds = max(0.0, time.monotonic() - worst.last_bar_received_monotonic)
        return {
            "status": worst.status,
            "silence_seconds": silence_seconds,
            "timeout_seconds": worst.stall_timeout_seconds,
            "blocked_message": worst.blocked_message,
            "last_bar_timestamp": _normalize_timestamp(worst.last_bar_timestamp) if worst.last_bar_timestamp else None,
            "streams": len(matches),
        }

    async def _register_stream(self, stream: _StreamHealth) -> None:
        self._active_streams[stream.stream_id] = stream
        async with self._health_lock:
            if self._health_task is None or self._health_task.done():
                self._health_task = asyncio.create_task(
                    self._health_loop(),
                    name="ibkr-bar-stream-health",
                )
            self._ensure_recovery_workers_locked()

    async def _unregister_stream(self, stream_id: int) -> None:
        self._active_streams.pop(stream_id, None)
        health_task: Optional[asyncio.Task] = None
        recovery_tasks: list[asyncio.Task] = []
        async with self._health_lock:
            if not self._active_streams:
                if self._health_task is not None and not self._health_task.done():
                    health_task = self._health_task
                recovery_tasks = [task for task in self._recovery_tasks if not task.done()]
                self._health_task = None
                self._recovery_tasks = []
                self._recovery_queue = asyncio.Queue()
                self._recovery_queued_groups.clear()
                self._recovery_in_progress_groups.clear()
        if health_task is not None:
            health_task.cancel()
            await asyncio.gather(health_task, return_exceptions=True)
        if recovery_tasks:
            for task in recovery_tasks:
                task.cancel()
            await asyncio.gather(*recovery_tasks, return_exceptions=True)

    def _mark_stream_bar(self, stream_id: int, bar_timestamp: datetime) -> None:
        stream = self._active_streams.get(stream_id)
        if stream is None:
            return
        now = time.monotonic()
        stream.last_bar_received_monotonic = now
        stream.last_bar_timestamp = _normalize_timestamp(bar_timestamp)
        if stream.status in {"stalled", "recovering", "failed", "blocked_competing_session"}:
            downtime_seconds = (
                max(0.0, now - stream.stalled_since_monotonic)
                if stream.stalled_since_monotonic is not None
                else 0.0
            )
            stream.status = "healthy"
            stream.stalled_since_monotonic = None
            stream.blocked_message = None
            stream.recover_attempt = 0
            stream.next_retry_monotonic = 0.0
            self._log_event(
                BarStreamRecovered.now(
                    symbol=stream.symbol,
                    bar_size=stream.bar_size,
                    use_rth=stream.use_rth,
                    downtime_seconds=downtime_seconds,
                )
            )
            return
        if stream.status == "starting":
            stream.status = "healthy"

    async def _health_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._health_poll_seconds)
                if not self._active_streams:
                    continue
                now = time.monotonic()
                newly_stalled_groups: set[tuple[str, bool]] = set()
                for stream in list(self._active_streams.values()):
                    if stream.closed:
                        continue
                    if stream.stall_timeout_seconds <= 0:
                        continue
                    if stream.last_bar_received_monotonic is None:
                        continue
                    silence_seconds = max(0.0, now - stream.last_bar_received_monotonic)
                    if silence_seconds > stream.stall_timeout_seconds:
                        if stream.status in {"healthy", "starting"}:
                            stream.status = "stalled"
                            stream.stalled_since_monotonic = now
                            self._log_event(
                                BarStreamStalled.now(
                                    symbol=stream.symbol,
                                    bar_size=stream.bar_size,
                                    use_rth=stream.use_rth,
                                    silence_seconds=silence_seconds,
                                    timeout_seconds=stream.stall_timeout_seconds,
                                )
                            )
                            newly_stalled_groups.add(self._stream_group_key(stream))
                        if not self._self_heal_enabled:
                            continue
                        if stream.status == "blocked_competing_session":
                            continue
                        if stream.recover_attempt >= self._recovery_max_attempts:
                            if stream.status != "failed":
                                stream.status = "failed"
                                self._log_event(
                                    BarStreamRecoveryFailed.now(
                                        symbol=stream.symbol,
                                        bar_size=stream.bar_size,
                                        use_rth=stream.use_rth,
                                        attempt=stream.recover_attempt,
                                        message="max recovery attempts reached",
                                        retry_in_seconds=0.0,
                                    )
                                )
                            continue
                        if now >= stream.next_retry_monotonic:
                            self._enqueue_recovery_group(self._stream_group_key(stream))
                if self._self_heal_enabled and len(newly_stalled_groups) >= self._stall_burst_count:
                    self._schedule_global_recovery_scan(
                        reason=f"stall_burst:{len(newly_stalled_groups)}",
                    )
        except asyncio.CancelledError:
            raise

    async def _recovery_loop(self, _worker_id: int) -> None:
        try:
            while True:
                group_key = await self._recovery_queue.get()
                self._recovery_queued_groups.discard(group_key)
                if group_key in self._recovery_in_progress_groups:
                    continue
                try:
                    self._recovery_in_progress_groups.add(group_key)
                    await self._recover_group(group_key)
                finally:
                    self._recovery_in_progress_groups.discard(group_key)
        except asyncio.CancelledError:
            raise

    async def _recover_group(
        self,
        group_key: tuple[str, bool],
    ) -> None:
        now = time.monotonic()
        streams = self._streams_for_group(group_key)
        if not streams:
            return
        for stream in streams:
            if stream.closed:
                continue
            if stream.status == "blocked_competing_session":
                continue
            # Keep group-level queueing, but only resubscribe streams that are not healthy.
            if stream.status not in {"stalled", "recovering", "failed"}:
                continue
            if now < stream.next_retry_monotonic:
                continue
            if stream.recover_attempt >= self._recovery_max_attempts:
                if stream.status != "failed":
                    stream.status = "failed"
                    self._log_event(
                        BarStreamRecoveryFailed.now(
                            symbol=stream.symbol,
                            bar_size=stream.bar_size,
                            use_rth=stream.use_rth,
                            attempt=stream.recover_attempt,
                            message="max recovery attempts reached",
                            retry_in_seconds=0.0,
                        )
                    )
                continue
            stream.recover_attempt += 1
            attempt = stream.recover_attempt
            stream.status = "recovering"
            self._log_event(
                BarStreamRecoveryStarted.now(
                    symbol=stream.symbol,
                    bar_size=stream.bar_size,
                    use_rth=stream.use_rth,
                    attempt=attempt,
                )
            )
            try:
                await self._resubscribe_stream(stream)
                stream.next_retry_monotonic = time.monotonic() + self._recovery_cooldown_seconds
            except Exception as exc:
                backoff_seconds = min(
                    60.0,
                    self._recovery_cooldown_seconds * (2 ** max(0, attempt - 1)),
                )
                stream.next_retry_monotonic = time.monotonic() + backoff_seconds
                stream.status = "stalled"
                self._log_event(
                    BarStreamRecoveryFailed.now(
                        symbol=stream.symbol,
                        bar_size=stream.bar_size,
                        use_rth=stream.use_rth,
                        attempt=attempt,
                        message=str(exc),
                        retry_in_seconds=backoff_seconds,
                    )
                )

    def _enqueue_recovery_group(self, group_key: tuple[str, bool]) -> None:
        if not self._self_heal_enabled:
            return
        if group_key in self._recovery_queued_groups:
            return
        if group_key in self._recovery_in_progress_groups:
            return
        self._recovery_queued_groups.add(group_key)
        self._recovery_queue.put_nowait(group_key)

    def _stream_group_key(self, stream: _StreamHealth) -> tuple[str, bool]:
        return (stream.symbol, stream.use_rth)

    def _streams_for_group(self, group_key: tuple[str, bool]) -> list[_StreamHealth]:
        symbol, use_rth = group_key
        members = [
            stream
            for stream in self._active_streams.values()
            if not stream.closed and stream.symbol == symbol and stream.use_rth == use_rth
        ]
        members.sort(
            key=lambda stream: (
                -(stream.expected_interval_seconds or 0.0),
                stream.bar_size,
                stream.stream_id,
            )
        )
        return members

    def _ensure_recovery_workers_locked(self) -> None:
        if not self._self_heal_enabled:
            return
        alive = [task for task in self._recovery_tasks if not task.done()]
        needed = self._recovery_max_concurrency - len(alive)
        for offset in range(max(0, needed)):
            worker_id = len(alive) + offset + 1
            alive.append(
                asyncio.create_task(
                    self._recovery_loop(worker_id),
                    name=f"ibkr-bar-stream-recovery-{worker_id}",
                )
            )
        self._recovery_tasks = alive

    def _on_gateway_message(
        self,
        req_id: Optional[int],
        code: Optional[int],
        message: Optional[str],
        advanced: Optional[str],
    ) -> None:
        del req_id, advanced
        if code is None:
            return
        text = (message or "").strip() or "gateway message"
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._handle_gateway_code, code, text)
                return
            except RuntimeError:
                pass
        self._handle_gateway_code(code, text)

    def _handle_gateway_code(self, code: int, message: str) -> None:
        if code == _COMPETING_SESSION_CODE:
            self._mark_competing_session_blocked(code=code, message=message)
            return
        if code in _RECONNECT_RECOVERY_CODES:
            self._clear_competing_session_block(code=code, message=message)
            self._schedule_global_recovery_scan(reason=f"gateway:{code}")

    def _mark_competing_session_blocked(self, *, code: int, message: str) -> None:
        now = time.monotonic()
        # TODO: Narrow this to affected subscriptions if IB supplies stable req_id mapping for keepUpToDate bars.
        for stream in list(self._active_streams.values()):
            if stream.closed:
                continue
            if stream.status == "blocked_competing_session":
                continue
            stream.status = "blocked_competing_session"
            stream.blocked_message = message
            if stream.stalled_since_monotonic is None:
                stream.stalled_since_monotonic = now
            stream.next_retry_monotonic = float("inf")
            self._log_event(
                BarStreamCompetingSessionBlocked.now(
                    symbol=stream.symbol,
                    bar_size=stream.bar_size,
                    use_rth=stream.use_rth,
                    code=code,
                    message=message,
                )
            )

    def _clear_competing_session_block(self, *, code: int, message: str) -> None:
        for stream in list(self._active_streams.values()):
            if stream.closed:
                continue
            if stream.status == "blocked_competing_session":
                stream.blocked_message = None
                stream.status = "stalled"
                if stream.stalled_since_monotonic is None:
                    stream.stalled_since_monotonic = time.monotonic()
                stream.next_retry_monotonic = 0.0
                self._log_event(
                    BarStreamCompetingSessionCleared.now(
                        symbol=stream.symbol,
                        bar_size=stream.bar_size,
                        use_rth=stream.use_rth,
                        code=code,
                        message=message,
                    )
                )
            if stream.status in {"stalled", "failed", "recovering"}:
                stream.next_retry_monotonic = 0.0
                self._enqueue_recovery_group(self._stream_group_key(stream))

    def _schedule_global_recovery_scan(self, *, reason: str) -> None:
        if not self._self_heal_enabled:
            return
        group_keys = sorted(
            {
                self._stream_group_key(stream)
                for stream in self._active_streams.values()
                if not stream.closed
            }
        )
        if not group_keys:
            return
        stream_count = sum(
            1
            for stream in self._active_streams.values()
            if not stream.closed
        )
        self._log_event(
            BarStreamRecoveryScanScheduled.now(
                reason=reason,
                groups=len(group_keys),
                streams=stream_count,
            )
        )
        for group_key in group_keys:
            self._enqueue_recovery_group(group_key)

    async def _resubscribe_stream(self, stream: _StreamHealth) -> None:
        if not self._ib.isConnected():
            raise RuntimeError("IBKR disconnected")
        old_bars = stream.bars
        detach_bar_update_event(old_bars, stream.on_bar)
        try:
            self._ib.cancelHistoricalData(old_bars)
        except Exception:
            pass
        new_bars = await self._request_live_bars(
            contract=stream.contract,
            duration=stream.duration,
            bar_size=stream.bar_size,
            use_rth=stream.use_rth,
        )
        current = self._active_streams.get(stream.stream_id)
        if current is None or current.closed:
            try:
                self._ib.cancelHistoricalData(new_bars)
            except Exception:
                pass
            return
        stream.bars = new_bars
        stream.last_count = len(new_bars)
        attach_bar_update_event(new_bars, stream.on_bar)

    async def _request_live_bars(
        self,
        *,
        contract: Stock,
        duration: str,
        bar_size: str,
        use_rth: bool,
    ) -> Any:
        return await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=use_rth,
            keepUpToDate=True,
        )

    def _log_event(self, event: object) -> None:
        if self._event_logger:
            self._event_logger(event)


def _env_float(name: str, *, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < minimum:
        return minimum
    return value


def _env_int(name: str, *, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return minimum
    return value


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _stall_timeout_seconds(
    *,
    expected_interval_seconds: Optional[float],
    multiplier: float,
    floor_seconds: float,
) -> float:
    if expected_interval_seconds is None or expected_interval_seconds <= 0:
        return floor_seconds
    return max(floor_seconds, expected_interval_seconds * multiplier)


def _status_rank(status: str) -> int:
    if status == "blocked_competing_session":
        return 5
    if status == "failed":
        return 4
    if status == "recovering":
        return 3
    if status == "stalled":
        return 2
    if status == "starting":
        return 1
    return 0

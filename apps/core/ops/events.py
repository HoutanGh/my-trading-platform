from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CliErrorLogged:
    message: str
    error_type: str
    traceback: str
    timestamp: datetime
    command: Optional[str] = None
    raw_input: Optional[str] = None

    @classmethod
    def now(
        cls,
        *,
        message: str,
        error_type: str,
        traceback: str,
        command: Optional[str] = None,
        raw_input: Optional[str] = None,
    ) -> "CliErrorLogged":
        return cls(
            message=message,
            error_type=error_type,
            traceback=traceback,
            timestamp=_now(),
            command=command,
            raw_input=raw_input,
        )


@dataclass(frozen=True)
class IbGatewayLog:
    code: Optional[int]
    message: Optional[str]
    req_id: Optional[int]
    timestamp: datetime
    host: Optional[str] = None
    port: Optional[int] = None
    client_id: Optional[int] = None
    advanced: Optional[str] = None

    @classmethod
    def now(
        cls,
        *,
        code: Optional[int],
        message: Optional[str],
        req_id: Optional[int],
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        advanced: Optional[str] = None,
    ) -> "IbGatewayLog":
        return cls(
            code=code,
            message=message,
            req_id=req_id,
            timestamp=_now(),
            host=host,
            port=port,
            client_id=client_id,
            advanced=advanced,
        )


@dataclass(frozen=True)
class IbkrConnectionAttempt:
    host: str
    port: int
    client_id: int
    readonly: bool
    mode: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        host: str,
        port: int,
        client_id: int,
        readonly: bool,
        mode: Optional[str],
    ) -> "IbkrConnectionAttempt":
        return cls(
            host=host,
            port=port,
            client_id=client_id,
            readonly=readonly,
            mode=mode,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class IbkrConnectionEstablished:
    host: str
    port: int
    client_id: int
    readonly: bool
    server_version: Optional[int]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        host: str,
        port: int,
        client_id: int,
        readonly: bool,
        server_version: Optional[int],
    ) -> "IbkrConnectionEstablished":
        return cls(
            host=host,
            port=port,
            client_id=client_id,
            readonly=readonly,
            server_version=server_version,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class IbkrConnectionFailed:
    host: str
    port: int
    client_id: int
    readonly: bool
    mode: Optional[str]
    error_type: str
    message: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        host: str,
        port: int,
        client_id: int,
        readonly: bool,
        mode: Optional[str],
        error_type: str,
        message: str,
    ) -> "IbkrConnectionFailed":
        return cls(
            host=host,
            port=port,
            client_id=client_id,
            readonly=readonly,
            mode=mode,
            error_type=error_type,
            message=message,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class IbkrConnectionClosed:
    host: str
    port: int
    client_id: int
    readonly: bool
    reason: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        host: str,
        port: int,
        client_id: int,
        readonly: bool,
        reason: Optional[str] = None,
    ) -> "IbkrConnectionClosed":
        return cls(
            host=host,
            port=port,
            client_id=client_id,
            readonly=readonly,
            reason=reason,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class OrphanExitOrderDetected:
    trigger: str
    action: str
    scope: str
    order_id: Optional[int]
    parent_order_id: Optional[int]
    account: Optional[str]
    symbol: str
    status: Optional[str]
    remaining_qty: Optional[float]
    client_tag: Optional[str]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        trigger: str,
        action: str,
        scope: str,
        order_id: Optional[int],
        parent_order_id: Optional[int],
        account: Optional[str],
        symbol: str,
        status: Optional[str],
        remaining_qty: Optional[float],
        client_tag: Optional[str],
    ) -> "OrphanExitOrderDetected":
        return cls(
            trigger=trigger,
            action=action,
            scope=scope,
            order_id=order_id,
            parent_order_id=parent_order_id,
            account=account,
            symbol=symbol,
            status=status,
            remaining_qty=remaining_qty,
            client_tag=client_tag,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class OrphanExitOrderCancelled:
    trigger: str
    order_id: Optional[int]
    status: Optional[str]
    account: Optional[str]
    symbol: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        trigger: str,
        order_id: Optional[int],
        status: Optional[str],
        account: Optional[str],
        symbol: str,
    ) -> "OrphanExitOrderCancelled":
        return cls(
            trigger=trigger,
            order_id=order_id,
            status=status,
            account=account,
            symbol=symbol,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class OrphanExitOrderCancelFailed:
    trigger: str
    order_id: Optional[int]
    account: Optional[str]
    symbol: str
    error_type: str
    message: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        trigger: str,
        order_id: Optional[int],
        account: Optional[str],
        symbol: str,
        error_type: str,
        message: str,
    ) -> "OrphanExitOrderCancelFailed":
        return cls(
            trigger=trigger,
            order_id=order_id,
            account=account,
            symbol=symbol,
            error_type=error_type,
            message=message,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class OrphanExitReconciliationCompleted:
    trigger: str
    scope: str
    action: str
    active_order_count: int
    position_count: int
    orphan_count: int
    cancelled_count: int
    cancel_failed_count: int
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        trigger: str,
        scope: str,
        action: str,
        active_order_count: int,
        position_count: int,
        orphan_count: int,
        cancelled_count: int,
        cancel_failed_count: int,
    ) -> "OrphanExitReconciliationCompleted":
        return cls(
            trigger=trigger,
            scope=scope,
            action=action,
            active_order_count=active_order_count,
            position_count=position_count,
            orphan_count=orphan_count,
            cancelled_count=cancelled_count,
            cancel_failed_count=cancel_failed_count,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamStarted:
    symbol: str
    bar_size: str
    use_rth: bool
    timestamp: datetime

    @classmethod
    def now(cls, *, symbol: str, bar_size: str, use_rth: bool) -> "BarStreamStarted":
        return cls(symbol=symbol, bar_size=bar_size, use_rth=use_rth, timestamp=_now())


@dataclass(frozen=True)
class BarStreamStopped:
    symbol: str
    bar_size: str
    use_rth: bool
    reason: Optional[str]
    last_bar_timestamp: Optional[datetime]
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        reason: Optional[str] = None,
        last_bar_timestamp: Optional[datetime] = None,
    ) -> "BarStreamStopped":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            reason=reason,
            last_bar_timestamp=last_bar_timestamp,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamGapDetected:
    symbol: str
    bar_size: str
    use_rth: bool
    expected_interval_seconds: float
    actual_interval_seconds: float
    previous_bar_timestamp: datetime
    current_bar_timestamp: datetime
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        expected_interval_seconds: float,
        actual_interval_seconds: float,
        previous_bar_timestamp: datetime,
        current_bar_timestamp: datetime,
    ) -> "BarStreamGapDetected":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            expected_interval_seconds=expected_interval_seconds,
            actual_interval_seconds=actual_interval_seconds,
            previous_bar_timestamp=previous_bar_timestamp,
            current_bar_timestamp=current_bar_timestamp,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamLagDetected:
    symbol: str
    bar_size: str
    use_rth: bool
    lag_seconds: float
    bar_timestamp: datetime
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        lag_seconds: float,
        bar_timestamp: datetime,
    ) -> "BarStreamLagDetected":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            lag_seconds=lag_seconds,
            bar_timestamp=bar_timestamp,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamStalled:
    symbol: str
    bar_size: str
    use_rth: bool
    silence_seconds: float
    timeout_seconds: float
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        silence_seconds: float,
        timeout_seconds: float,
    ) -> "BarStreamStalled":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            silence_seconds=silence_seconds,
            timeout_seconds=timeout_seconds,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamRecovered:
    symbol: str
    bar_size: str
    use_rth: bool
    downtime_seconds: float
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        downtime_seconds: float,
    ) -> "BarStreamRecovered":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            downtime_seconds=downtime_seconds,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamRecoveryStarted:
    symbol: str
    bar_size: str
    use_rth: bool
    attempt: int
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        attempt: int,
    ) -> "BarStreamRecoveryStarted":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            attempt=attempt,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamRecoveryFailed:
    symbol: str
    bar_size: str
    use_rth: bool
    attempt: int
    message: str
    retry_in_seconds: float
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        attempt: int,
        message: str,
        retry_in_seconds: float,
    ) -> "BarStreamRecoveryFailed":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            attempt=attempt,
            message=message,
            retry_in_seconds=retry_in_seconds,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamCompetingSessionBlocked:
    symbol: str
    bar_size: str
    use_rth: bool
    code: int
    message: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        code: int,
        message: str,
    ) -> "BarStreamCompetingSessionBlocked":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            code=code,
            message=message,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamCompetingSessionCleared:
    symbol: str
    bar_size: str
    use_rth: bool
    code: int
    message: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        symbol: str,
        bar_size: str,
        use_rth: bool,
        code: int,
        message: str,
    ) -> "BarStreamCompetingSessionCleared":
        return cls(
            symbol=symbol,
            bar_size=bar_size,
            use_rth=use_rth,
            code=code,
            message=message,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class BarStreamRecoveryScanScheduled:
    reason: str
    groups: int
    streams: int
    timestamp: datetime

    @classmethod
    def now(
        cls,
        *,
        reason: str,
        groups: int,
        streams: int,
    ) -> "BarStreamRecoveryScanScheduled":
        return cls(
            reason=reason,
            groups=groups,
            streams=streams,
            timestamp=_now(),
        )


@dataclass(frozen=True)
class IbGatewayRawLine:
    line: str
    source_path: str
    timestamp: datetime

    @classmethod
    def now(cls, *, line: str, source_path: str) -> "IbGatewayRawLine":
        return cls(line=line, source_path=source_path, timestamp=_now())

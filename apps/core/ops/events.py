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
class IbGatewayRawLine:
    line: str
    source_path: str
    timestamp: datetime

    @classmethod
    def now(cls, *, line: str, source_path: str) -> "IbGatewayRawLine":
        return cls(line=line, source_path=source_path, timestamp=_now())

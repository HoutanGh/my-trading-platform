from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from appsv2.core.pnl.models import PnlIngestResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PnlIngestStarted:
    csv_path: str
    account: str
    source: str
    timestamp: datetime

    @classmethod
    def now(cls, csv_path: str, account: str, source: str) -> "PnlIngestStarted":
        return cls(csv_path=csv_path, account=account, source=source, timestamp=_now())


@dataclass(frozen=True)
class PnlIngestFinished:
    result: PnlIngestResult
    timestamp: datetime

    @classmethod
    def now(cls, result: PnlIngestResult) -> "PnlIngestFinished":
        return cls(result=result, timestamp=_now())


@dataclass(frozen=True)
class PnlIngestFailed:
    csv_path: str
    account: str
    source: str
    error: str
    timestamp: datetime

    @classmethod
    def now(
        cls,
        csv_path: str,
        account: str,
        source: str,
        error: str,
    ) -> "PnlIngestFailed":
        return cls(
            csv_path=csv_path,
            account=account,
            source=source,
            error=error,
            timestamp=_now(),
        )

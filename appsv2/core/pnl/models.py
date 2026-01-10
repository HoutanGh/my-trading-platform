from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyPnlRow:
    account: str
    trade_date: date
    realized_pnl: float
    source: str


@dataclass(frozen=True)
class PnlIngestResult:
    account: str
    source: str
    csv_path: str
    rows_read: int
    rows_used: int
    days_ingested: int

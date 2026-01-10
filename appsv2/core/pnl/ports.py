from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional, Protocol

from appsv2.core.pnl.models import DailyPnlRow, PnlIngestResult


class DailyPnlStore(Protocol):
    def upsert_daily_pnl(self, row: DailyPnlRow) -> None:
        raise NotImplementedError

    def fetch_daily_pnl(
        self,
        account: str,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> list[DailyPnlRow]:
        raise NotImplementedError


class FlexPnlIngestor(Protocol):
    def ingest(
        self,
        csv_path: Path,
        account: str,
        source: str = "flex",
    ) -> PnlIngestResult:
        raise NotImplementedError

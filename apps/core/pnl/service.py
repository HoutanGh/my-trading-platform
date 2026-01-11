from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from apps.core.orders.ports import EventBus
from apps.core.pnl.events import PnlIngestFailed, PnlIngestFinished, PnlIngestStarted
from apps.core.pnl.models import DailyPnlRow, PnlIngestResult
from apps.core.pnl.ports import DailyPnlStore, FlexPnlIngestor


class PnlService:
    def __init__(
        self,
        ingestor: FlexPnlIngestor,
        store: DailyPnlStore,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._ingestor = ingestor
        self._store = store
        self._event_bus = event_bus

    def ingest_flex(
        self,
        csv_path: Path,
        account: str,
        source: str = "flex",
    ) -> PnlIngestResult:
        if self._event_bus:
            self._event_bus.publish(PnlIngestStarted.now(str(csv_path), account, source))
        try:
            result = self._ingestor.ingest(csv_path, account, source)
        except Exception as exc:
            if self._event_bus:
                self._event_bus.publish(
                    PnlIngestFailed.now(str(csv_path), account, source, str(exc))
                )
            raise
        if self._event_bus:
            self._event_bus.publish(PnlIngestFinished.now(result))
        return result

    def get_daily_pnl(
        self,
        account: str,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> list[DailyPnlRow]:
        return self._store.fetch_daily_pnl(account, start_date, end_date)

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from apps.adapters.pnl.db import ensure_schema
from apps.core.pnl.models import DailyPnlRow, PnlIngestResult
from apps.core.pnl.ports import DailyPnlStore

logger = logging.getLogger(__name__)


class FlexCsvPnlIngestor:
    def __init__(self, store: DailyPnlStore) -> None:
        self._store = store

    def ingest(
        self,
        csv_path: Path,
        account: str,
        source: str = "flex",
    ) -> PnlIngestResult:
        if not csv_path.is_file():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        ensure_schema()

        df = pd.read_csv(csv_path)
        rows_read = len(df)

        required_cols = {"TradeDate", "FifoPnlRealized"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"CSV is missing required columns: {missing}")

        df["TradeDate"] = pd.to_datetime(df["TradeDate"], errors="coerce").dt.date
        df["FifoPnlRealized"] = pd.to_numeric(df["FifoPnlRealized"], errors="coerce")
        df = df.dropna(subset=["TradeDate", "FifoPnlRealized"])
        rows_used = len(df)

        grouped = (
            df.groupby("TradeDate", as_index=False)["FifoPnlRealized"]
            .sum()
            .rename(columns={"FifoPnlRealized": "realized_pnl"})
        )
        days_ingested = len(grouped)

        logger.info(
            "Flex ingest csv=%s account=%s rows_read=%s rows_used=%s days=%s",
            csv_path,
            account,
            rows_read,
            rows_used,
            days_ingested,
        )

        for row in grouped.itertuples(index=False):
            self._store.upsert_daily_pnl(
                DailyPnlRow(
                    account=account,
                    trade_date=row.TradeDate,
                    realized_pnl=float(row.realized_pnl),
                    source=source,
                )
            )

        return PnlIngestResult(
            account=account,
            source=source,
            csv_path=str(csv_path),
            rows_read=rows_read,
            rows_used=rows_used,
            days_ingested=days_ingested,
        )

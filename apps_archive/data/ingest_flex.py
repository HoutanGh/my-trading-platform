# apps/data/ingest_flex.py
import argparse
from pathlib import Path

import pandas as pd

from apps.data.db import ensure_schema
from apps.data.upload import upsert_daily_pnl


def load_flex_history(csv_path: Path, account: str, source: str = "flex") -> None:
    """
    Read a Flex CSV, aggregate realized P&L per TradeDate, and upsert into daily_pnl.
    Expects at least columns: TradeDate, FifoPnlRealized.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Make sure the table exists
    ensure_schema()

    # 1) Read CSV
    df = pd.read_csv(csv_path)

    # 2) Basic column checks
    required_cols = {"TradeDate", "FifoPnlRealized"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # 3) Clean/parse
    df["TradeDate"] = pd.to_datetime(df["TradeDate"], errors="coerce").dt.date
    df["FifoPnlRealized"] = pd.to_numeric(df["FifoPnlRealized"], errors="coerce")

    # Drop rows where date or pnl couldn't be parsed
    df = df.dropna(subset=["TradeDate", "FifoPnlRealized"])

    # 4) Aggregate per day
    grouped = (
        df.groupby("TradeDate", as_index=False)["FifoPnlRealized"]
        .sum()
        .rename(columns={"FifoPnlRealized": "realized_pnl"})
    )

    # 5) Upsert into daily_pnl using shared helper
    for row in grouped.itertuples(index=False):
        upsert_daily_pnl(account, row.TradeDate, float(row.realized_pnl), source)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill daily P&L from an IB Flex CSV"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/raw/Daily_PL.csv"),
        help="Path to Flex CSV export (default: data/raw/Daily_PL.csv)",
    )
    parser.add_argument(
        "--account",
        type=str,
        required=True,
        help="IB account id (e.g. DU123456)",
    )
    args = parser.parse_args()

    load_flex_history(args.csv, args.account)


if __name__ == "__main__":
    main()

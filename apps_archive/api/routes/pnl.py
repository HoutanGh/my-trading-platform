import logging
from datetime import date
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.deps import db_conn

router = APIRouter(prefix="/pnl", tags=["pnl"])
logger = logging.getLogger(__name__)


@router.get("/daily")
def get_daily_pnl(
    account: str = Query(..., description="Account id to fetch P&L for"),
    start_date: Optional[date] = Query(
        None, description="Optional start date (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        None, description="Optional end date (YYYY-MM-DD)"
    ),
    conn: psycopg.Connection = Depends(db_conn),
) -> list[dict]:
    """
    Define a GET endpoint at /pnl/daily that queries the daily_pnl table,
    optionally filters by start and end date, and returns objects with
    trade_date and realized_pnl fields.
    """
    if start_date and end_date and end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must be on or after start_date",
        )

    logger.info(
        "Fetching daily_pnl account=%s start_date=%s end_date=%s",
        account,
        start_date,
        end_date,
    )

    conditions = ["account = %s"]
    params = [account]

    if start_date:
        conditions.append("trade_date >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("trade_date <= %s")
        params.append(end_date)

    sql = """
    SELECT account, trade_date, realized_pnl, source
    FROM daily_pnl
    WHERE {where_clause}
    ORDER BY trade_date
    """.format(
        where_clause=" AND ".join(conditions)
    )

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    results: list[dict] = []
    for account_val, trade_date, realized_pnl, source in rows:
        results.append(
            {
                "account": account_val,
                "trade_date": trade_date.isoformat(),
                "realized_pnl": float(realized_pnl),
                "source": source,
            }
        )

    return results

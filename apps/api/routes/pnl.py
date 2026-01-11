import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.deps import get_pnl_service
from apps.core.pnl.service import PnlService

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
    service: PnlService = Depends(get_pnl_service),
) -> list[dict]:
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

    rows = service.get_daily_pnl(account, start_date, end_date)
    results = [
        {
            "account": row.account,
            "trade_date": row.trade_date.isoformat(),
            "realized_pnl": float(row.realized_pnl),
            "source": row.source,
        }
        for row in rows
    ]

    return results

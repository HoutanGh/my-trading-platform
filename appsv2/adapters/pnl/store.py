from __future__ import annotations

from datetime import date
from typing import Optional

from appsv2.adapters.pnl.db import get_conn
from appsv2.core.pnl.models import DailyPnlRow


class PostgresDailyPnlStore:
    def upsert_daily_pnl(self, row: DailyPnlRow) -> None:
        sql = """
        INSERT INTO daily_pnl (account, trade_date, realized_pnl, source)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (account, trade_date, source)
        DO UPDATE SET realized_pnl = EXCLUDED.realized_pnl;
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (row.account, row.trade_date, row.realized_pnl, row.source),
                )
            conn.commit()

    def fetch_daily_pnl(
        self,
        account: str,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> list[DailyPnlRow]:
        conditions = ["account = %s"]
        params: list[object] = [account]

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

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        return [
            DailyPnlRow(
                account=account_val,
                trade_date=trade_date,
                realized_pnl=float(realized_pnl),
                source=source,
            )
            for account_val, trade_date, realized_pnl, source in rows
        ]

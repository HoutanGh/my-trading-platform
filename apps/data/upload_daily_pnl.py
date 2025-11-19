from datetime import date
from apps.data.db import get_conn, ensure_schema  # if you put db.py there


def upsert_daily_pnl(account: str, trade_date: date, realized_pnl: float, source: str) -> None:
    ensure_schema()  # no-op if table already exists
    sql = """
    INSERT INTO daily_pnl (account, trade_date, realized_pnl, source)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (account, trade_date, source)
    DO UPDATE SET realized_pnl = EXCLUDED.realized_pnl;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (account, trade_date, realized_pnl, source))
        conn.commit()


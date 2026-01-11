import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
import psycopg

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(DATABASE_URL) as conn:
        yield conn


def ensure_schema() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS daily_pnl (
        id BIGSERIAL PRIMARY KEY,
        account TEXT NOT NULL,
        trade_date DATE NOT NULL,
        realized_pnl DOUBLE PRECISION NOT NULL,
        source TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account, trade_date, source)
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

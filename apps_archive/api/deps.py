from typing import Iterator

import psycopg

from apps.data.db import get_conn


def db_conn() -> Iterator[psycopg.Connection]:
    """
    FastAPI-friendly wrapper around the shared get_conn() context manager.

    Usage in routes:
        def handler(conn: psycopg.Connection = Depends(db_conn)):
            ...
    """
    with get_conn() as conn:
        yield conn

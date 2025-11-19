import os
from contextlib import contextmanager
from typing import Iterator

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")

print(DATABASE_URL)
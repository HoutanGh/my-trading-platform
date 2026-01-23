from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator


async def tail_ib_gateway_log(
    path: str,
    *,
    poll_interval: float = 0.5,
    start_at_end: bool = True,
) -> AsyncIterator[str]:
    log_path = os.path.expanduser(path)
    position = 0
    if start_at_end:
        try:
            position = os.path.getsize(log_path)
        except FileNotFoundError:
            position = 0

    while True:
        try:
            size = os.path.getsize(log_path)
            if size < position:
                position = 0
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                while True:
                    line = handle.readline()
                    if line:
                        position = handle.tell()
                        yield line.rstrip("\n")
                    else:
                        break
        except FileNotFoundError:
            position = 0
        await asyncio.sleep(poll_interval)

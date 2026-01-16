from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PositionSnapshot:
    account: str
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    qty: float
    avg_cost: Optional[float] = None
    market_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    con_id: Optional[int] = None

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class ActiveOrderSnapshot:
    order_id: Optional[int]
    perm_id: Optional[int]
    parent_order_id: Optional[int]
    client_id: Optional[int]
    account: Optional[str]
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    side: Optional[str]
    order_type: Optional[str]
    qty: Optional[float]
    filled_qty: Optional[float]
    remaining_qty: Optional[float]
    limit_price: Optional[float]
    stop_price: Optional[float]
    status: Optional[str]
    tif: Optional[str]
    outside_rth: Optional[bool]
    client_tag: Optional[str]
    updated_at: Optional[datetime] = None

# apps/ib/orders.py
from ib_insync import LimitOrder, MarketOrder, StopOrder
from typing import Tuple, Optional
import uuid

def build_buy_bracket(
    qty: int,
    entry_price: Optional[float],   # None = Market
    tp_price: float,
    sl_price: float,
    tif: str = "DAY"
) -> Tuple[object, object, object]:
    """
    Returns (parent, take_profit, stop_loss) orders for a BUY bracket.
    - Parent: Market if entry_price is None, else Limit
    - Children share OCA group; last child transmits entire bracket.
    """
    oca = f"BRKT-{uuid.uuid4().hex[:10]}" # One Cancels  All group ID
    if entry_price is None:
        parent = MarketOrder("BUY", qty, tif=tif)
    else:
        parent = LimitOrder("BUY", qty, round(entry_price, 2), tif=tif)
    parent.transmit = False

    take_profit = LimitOrder("SELL", qty, round(tp_price, 2), tif=tif)
    take_profit.ocaGroup = oca
    take_profit.transmit = False

    stop_loss = StopOrder("SELL", qty, round(sl_price, 2), tif=tif)
    stop_loss.ocaGroup = oca
    stop_loss.transmit = True   # last child will transmit all

    return parent, take_profit, stop_loss

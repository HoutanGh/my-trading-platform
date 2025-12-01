import asyncio
import time
from typing import Optional

from ib_insync import MarketOrder
from loguru import logger

from apps.ib.client import IBClient
from apps.ib.journal import write_event
from apps.ib.orders import build_buy_bracket


class BrokerService:
    """High-level trading operations built on top of the IB client adapter."""

    def __init__(self, client: IBClient) -> None:
        self.client = client

    def place_buy_bracket_pct(
        self,
        symbol: str,
        qty: int,
        tp_pct: float,
        sl_pct: float,
        *,
        limit_offset: Optional[float] = None,
    ) -> None:
        """Submit a BUY bracket using percentage-based TP/SL levels."""
        contract = self.client.qualify_stock(symbol)
        ref_price = self.client.get_reference_price(contract)
        entry_price = None if limit_offset is None else round(ref_price - abs(limit_offset), 2)
        tp_price = round(ref_price * (1.0 + abs(tp_pct)), 2)
        sl_price = round(ref_price * (1.0 - abs(sl_pct)), 2)

        parent, tp, sl = build_buy_bracket(qty, entry_price, tp_price, sl_price)

        t0 = time.time()
        trade_parent = self.client.place_order(contract, parent)
        parent_order_id = self.client.wait_for_order_id(trade_parent)
        tp.parentId = parent_order_id
        sl.parentId = parent_order_id
        self.client.place_order(contract, tp)
        self.client.place_order(contract, sl)

        write_event(
            {
                "type": "INTENT",
                "side": "BUY",
                "symbol": symbol,
                "qty": qty,
                "refPrice": ref_price,
                "entry": entry_price or "MKT",
                "tp": tp_price,
                "sl": sl_price,
                "parentOrderId": parent_order_id,
            }
        )

        ack = self.client.wait_for_order_status(trade_parent) or "submitted"
        t1 = time.time()
        write_event(
            {
                "type": "ACK",
                "orderId": parent_order_id,
                "status": ack,
                "latency_ms_send": int((t1 - t0) * 1000),
            }
        )

        logger.info(
            f"BUY {symbol} x{qty} @ {entry_price or 'MKT'} ⇒ TP {tp_price} SL {sl_price} (orderId={parent_order_id})"
        )

    async def place_buy_bracket_pct_async(
        self,
        symbol: str,
        qty: int,
        tp_pct: float,
        sl_pct: float,
        *,
        limit_offset: Optional[float] = None,
    ) -> None:
        """Async version of bracket buy for use inside the asyncio CLI."""
        logger.info("Qualifying contract for {}", symbol)
        contract = await self.client.qualify_stock_async(symbol)
        logger.info("Fetching reference price for {}", symbol)
        ref_price = await self.client.get_reference_price_async(contract)
        entry_price = None if limit_offset is None else round(ref_price - abs(limit_offset), 2)
        tp_price = round(ref_price * (1.0 + abs(tp_pct)), 2)
        sl_price = round(ref_price * (1.0 - abs(sl_pct)), 2)

        parent, tp, sl = build_buy_bracket(qty, entry_price, tp_price, sl_price)

        t0 = time.time()
        trade_parent = self.client.place_order(contract, parent)
        parent_order_id = await self.client.wait_for_order_id_async(trade_parent)
        tp.parentId = parent_order_id
        sl.parentId = parent_order_id
        self.client.place_order(contract, tp)
        self.client.place_order(contract, sl)

        write_event(
            {
                "type": "INTENT",
                "side": "BUY",
                "symbol": symbol,
                "qty": qty,
                "refPrice": ref_price,
                "entry": entry_price or "MKT",
                "tp": tp_price,
                "sl": sl_price,
                "parentOrderId": parent_order_id,
            }
        )

        ack = await self.client.wait_for_order_status_async(trade_parent) or "submitted"
        t1 = time.time()
        write_event(
            {
                "type": "ACK",
                "orderId": parent_order_id,
                "status": ack,
                "latency_ms_send": int((t1 - t0) * 1000),
            }
        )

        logger.info(
            f"BUY {symbol} x{qty} @ {entry_price or 'MKT'} ⇒ TP {tp_price} SL {sl_price} (orderId={parent_order_id})"
        )

    def place_breakout_market_buy(self, symbol: str, qty: int) -> int:
        """Submit a simple market BUY for breakout MVP (no TP/SL)."""
        contract = self.client.qualify_stock(symbol)
        order = MarketOrder("BUY", qty)

        t0 = time.time()
        trade = self.client.place_order(contract, order)
        order_id = self.client.wait_for_order_id(trade)

        write_event(
            {
                "type": "INTENT",
                "side": "BUY_BREAKOUT_MKT",
                "symbol": symbol,
                "qty": qty,
                "entry": "MKT",
                "orderId": order_id,
            }
        )

        ack = self.client.wait_for_order_status(trade) or "submitted"
        t1 = time.time()
        write_event(
            {
                "type": "ACK",
                "orderId": order_id,
                "status": ack,
                "latency_ms_send": int((t1 - t0) * 1000),
            }
        )

        logger.info(f"BUY breakout {symbol} x{qty} @ MKT (orderId={order_id})")
        return order_id

    def cancel_all_orders(self, count: Optional[int] = None) -> int:
        """Cancel all open trades (if requested) and journal the event."""
        if count is None:
            count = self.client.cancel_all()
        write_event({"type": "CANCEL_ALL", "count": count})
        logger.info(f"Cancel requested for {count} open orders")
        return count

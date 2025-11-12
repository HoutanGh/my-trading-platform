# apps/ib/client.py
import os, time
from typing import Optional
from dotenv import load_dotenv
from ib_insync import IB, Stock, Ticker
from loguru import logger
from .journal import write_event
from .orders import build_buy_bracket

load_dotenv()

IB_HOST = os.getenv("IB_HOST", "172.21.224.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1001"))
PAPER_ONLY = os.getenv("PAPER_ONLY", "1") == "1"

def _assert_paper_mode():
    if PAPER_ONLY and IB_PORT != 7497:
        raise SystemExit("PAPER_ONLY=1 but IB_PORT != 7497. Refusing to run.")

class Client:
    def __init__(self):
        self.ib = IB()

    def connect(self):
        logger.info(f"Connecting IB {IB_HOST}:{IB_PORT} clientId={IB_CLIENT_ID} ...")
        self.ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, readonly=False, timeout=5)
        logger.success("Connected.")

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()

    def _qualify(self, symbol: str):
        c = Stock(symbol, "SMART", "USD")
        [qc] = self.ib.qualifyContracts(c)
        return qc

    def _ref_price(self, contract) -> float:
        # Use last if available; else mid(bid,ask)
        t: Ticker = self.ib.reqMktData(contract, "", False, False)
        for _ in range(30):
            self.ib.sleep(0.1)
            last, bid, ask = t.last, t.bid, t.ask
            if last and last > 0:
                return float(last)
            if bid and ask and bid > 0 and ask > 0:
                return float((bid + ask) / 2)
        raise RuntimeError("No reference price (check market data or symbol)")

    def place_buy_bracket_pct(self, symbol: str, qty: int, tp_pct: float, sl_pct: float, limit_offset: Optional[float]=None):
        contract = self._qualify(symbol)
        px = self._ref_price(contract)
        entry_price = None if limit_offset is None else round(px - abs(limit_offset), 2)
        tp_price = round(px * (1.0 + abs(tp_pct)), 2)
        sl_price = round(px * (1.0 - abs(sl_pct)), 2)

        parent, tp, sl = build_buy_bracket(qty, entry_price, tp_price, sl_price)

        t0 = time.time()
        trade_parent = self.ib.placeOrder(contract, parent)
        self.ib.sleep(0.05)  # ensure orderId assigned
        tp.parentId = trade_parent.order.orderId
        sl.parentId = trade_parent.order.orderId
        trade_tp = self.ib.placeOrder(contract, tp)
        trade_sl = self.ib.placeOrder(contract, sl)

        write_event({
            "type": "INTENT", "side": "BUY", "symbol": symbol, "qty": qty,
            "refPrice": px, "entry": entry_price or "MKT", "tp": tp_price, "sl": sl_price,
            "parentOrderId": trade_parent.order.orderId
        })

        # small ack wait
        for _ in range(30):
            if trade_parent.orderStatus.status:
                break
            self.ib.sleep(0.1)
        ack = trade_parent.orderStatus.status or "submitted"
        t1 = time.time()
        write_event({"type": "ACK", "orderId": trade_parent.order.orderId, "status": ack, "latency_ms_send": int((t1 - t0)*1000)})

        logger.info(f"BUY {symbol} x{qty} @ {entry_price or 'MKT'} ⇒ TP {tp_price} SL {sl_price} (orderId={trade_parent.order.orderId})")

    def cancel_all(self):
        trades = list(self.ib.openTrades())
        for tr in trades:
            try:
                self.ib.cancelOrder(tr.order)
            except Exception as e:
                logger.error(f"Cancel error for orderId={tr.order.orderId}: {e}")
        write_event({"type": "CANCEL_ALL", "count": len(trades)})

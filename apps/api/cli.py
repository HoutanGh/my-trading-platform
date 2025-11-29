# apps/api/app.py
import os
from typing import Optional

from dotenv import load_dotenv

from apps.broker.service import BrokerService
from apps.ib.client import IBClient
from apps.strategies.breakout.breakout_watcher import run_breakout_watcher

load_dotenv()

SYMBOL = os.getenv("DEFAULT_SYMBOL", "AAPL")
QTY = int(os.getenv("DEFAULT_QTY", "1"))
TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "0.01"))
SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "0.005"))
IB_HOST = os.getenv("IB_HOST", "172.21.224.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1001"))
PAPER_ONLY = os.getenv("PAPER_ONLY", "1") == "1"


def _assert_paper_mode(paper_only: bool, port: int) -> None:
    if paper_only and port != 7497:
        raise SystemExit("PAPER_ONLY=1 but IB_PORT != 7497. Refusing to run.")

def _prompt_float(prompt: str, *, default: Optional[float] = None) -> float:
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")

def _prompt_int(prompt: str, *, default: int) -> int:
    while True:
        raw = input(prompt).strip()
        if raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            print("Please enter a whole number.")

def run():
    print("\n=== SPEEDRUN PHASE-1 (PAPER) ===")
    print(f"Default: SYMBOL={SYMBOL} QTY={QTY} TP={TP_PCT*100:.2f}% SL={SL_PCT*100:.2f}%")
    print("Commands: [b]=buy bracket  [w]=breakout watcher  [k]=cancel all  [q]=quit\n")

    _assert_paper_mode(PAPER_ONLY, IB_PORT)
    client = IBClient()
    service = BrokerService(client)
    try:
        client.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)
        while True:
            cmd = input("> ").strip().lower()
            if cmd == "b":
                service.place_buy_bracket_pct(
                    SYMBOL,
                    QTY,
                    TP_PCT,
                    SL_PCT,
                    limit_offset=None,
                )
            elif cmd == "w":
                symbol = input(f"Symbol [{SYMBOL}]: ").strip().upper() or SYMBOL
                level = _prompt_float("Breakout level: ")
                qty = _prompt_int(f"Shares [{QTY}]: ", default=QTY)
                print(f"Starting breakout watcher for {symbol} at {level} with qty={qty} ...")
                try:
                    run_breakout_watcher(symbol, level, client, service, qty=qty)
                except Exception as exc:
                    print(f"Error in breakout watcher: {exc}")
            elif cmd == "k":
                count = client.cancel_all()
                service.cancel_all_orders(count=count)
                print(f"Canceled {count} open orders.")
            elif cmd == "q":
                break
            elif cmd == "":
                continue
            else:
                print("Unknown command. Use b/k/q.")
    finally:
        client.disconnect()
        print("Disconnected. Bye.")

if __name__ == "__main__":
    run()

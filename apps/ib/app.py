# apps/ib/app.py
import os
from dotenv import load_dotenv
from loguru import logger
from .client import Client, _assert_paper_mode

load_dotenv()
SYMBOL = os.getenv("DEFAULT_SYMBOL", "AAPL")
QTY = int(os.getenv("DEFAULT_QTY", "1"))
TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "0.01"))
SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "0.005"))

def run():
    print("\n=== SPEEDRUN PHASE-1 (PAPER) ===")
    print(f"Default: SYMBOL={SYMBOL} QTY={QTY} TP={TP_PCT*100:.2f}% SL={SL_PCT*100:.2f}%")
    print("Commands: [b]=buy bracket  [k]=cancel all  [q]=quit\n")

    _assert_paper_mode()
    client = Client()
    try:
        client.connect()
        while True:
            cmd = input("> ").strip().lower()
            if cmd == "b":
                client.place_buy_bracket_pct(SYMBOL, QTY, TP_PCT, SL_PCT, limit_offset=None)
            elif cmd == "k":
                client.cancel_all()
                print("Canceled all open orders.")
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

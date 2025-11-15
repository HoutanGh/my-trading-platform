# apps/api/app.py
import os

from dotenv import load_dotenv

from apps.broker.service import BrokerService
from apps.ib.client import IBClient

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

def run():
    print("\n=== SPEEDRUN PHASE-1 (PAPER) ===")
    print(f"Default: SYMBOL={SYMBOL} QTY={QTY} TP={TP_PCT*100:.2f}% SL={SL_PCT*100:.2f}%")
    print("Commands: [b]=buy bracket  [k]=cancel all  [q]=quit\n")

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

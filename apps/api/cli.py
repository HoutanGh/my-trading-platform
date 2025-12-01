# apps/api/app.py
import asyncio
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from ib_insync import IB
from loguru import logger

from apps.broker.service import BrokerService
from apps.engine.breakout_watcher import run_breakout_watcher
from apps.ib.client import IBClient

load_dotenv()

# Log to stdout so CLI sessions show progress/errors.
logger.remove()
logger.add(sys.stdout, level=os.getenv("LOG_LEVEL", "INFO"))

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

def _menu(prompt: str, options: dict[str, str]) -> str:
    """Simple text menu helper."""
    print(prompt)
    for key, desc in options.items():
        print(f"  [{key}] {desc}")
    print()
    return input("> ").strip().lower()


async def _manual_trading_loop(service: BrokerService, client: IBClient) -> None:
    """Existing manual b/k/q loop."""
    print("Manual trading: [b]=buy bracket  [k]=cancel all  [q]=back\n")
    while True:
        cmd = (await asyncio.to_thread(input, "> ")).strip().lower()
        if cmd == "b":
            logger.info(
                "Manual buy: qualifying {}, qty={}, TP%={}, SL%={}",
                SYMBOL,
                QTY,
                TP_PCT,
                SL_PCT,
            )
            try:
                await service.place_buy_bracket_pct_async(
                    SYMBOL,
                    QTY,
                    TP_PCT,
                    SL_PCT,
                    limit_offset=None,
                )
            except Exception as exc:
                logger.error("Manual buy failed: %s", exc)
        elif cmd == "k":
            count = client.cancel_all()
            service.cancel_all_orders(count=count)
            print(f"Canceled {count} open orders.")
        elif cmd == "q":
            return
        elif cmd == "":
            continue
        else:
            print("Unknown command. Use b/k/q.")


def _breakout_live(service: BrokerService, client: IBClient) -> None:
    """Breakout strategy – live/paper via watcher."""
    symbol = input(f"Symbol [{SYMBOL}]: ").strip().upper() or SYMBOL
    level = _prompt_float("Breakout level: ")
    qty = _prompt_int(f"Shares [{QTY}]: ", default=QTY)
    print(f"Starting breakout watcher for {symbol} at {level} with qty={qty} ...")
    try:
        run_breakout_watcher(symbol, level, client, service, qty=qty)
    except Exception as exc:
        print(f"Error in breakout watcher: {exc}")


def _breakout_menu(service: BrokerService, client: IBClient) -> None:
    """Menu for breakout strategy: live or backtest (stub)."""
    while True:
        choice = _menu(
            "Breakout strategy:",
            {
                "1": "Live/Paper breakout watcher",
                "2": "Backtest (coming soon)",
                "q": "Back",
            },
        )
        if choice == "1":
            _breakout_live(service, client)
        elif choice == "2":
            print("Backtest mode not implemented yet.")
        elif choice == "q":
            return
        else:
            print("Unknown choice.")


async def run():
    print("\n=== SPEEDRUN PHASE-1 (PAPER) ===")
    print(f"Default: SYMBOL={SYMBOL} QTY={QTY} TP={TP_PCT*100:.2f}% SL={SL_PCT*100:.2f}%")
    print("Modes: manual trading, strategies (breakout).\n")

    _assert_paper_mode(PAPER_ONLY, IB_PORT)
    ib = IB()
    client = IBClient(ib)
    service = BrokerService(client)
    try:
        await ib.connectAsync(
            IB_HOST,
            IB_PORT,
            clientId=IB_CLIENT_ID,
            timeout=5,
        )
        while True:
            choice = _menu(
                "Main menu:",
                {
                    "1": "Manual trading",
                    "2": "Strategies",
                    "q": "Quit",
                },
            )
            if choice == "1":
                await _manual_trading_loop(service, client)
            elif choice == "2":
                _breakout_menu(service, client)
            elif choice == "q":
                break
            else:
                print("Unknown choice.")
    finally:
        client.disconnect()
        print("Disconnected. Bye.")

if __name__ == "__main__":
    asyncio.run(run())

import os

from dotenv import load_dotenv
from loguru import logger

from apps.ib.client import IBClient


load_dotenv()

IB_HOST = os.getenv("IB_HOST", "172.21.224.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1001"))
IB_ACCOUNT = os.getenv("IB_ACCOUNT")


def print_positions_and_pnl() -> None:
    """Connect to IB, print managed accounts, positions, and P&L snapshot."""
    logger.info(
        "Connecting to IB host=%s port=%s clientId=%s", IB_HOST, IB_PORT, IB_CLIENT_ID
    )
    client = IBClient()
    client.connect(IB_HOST, IB_PORT, IB_CLIENT_ID, readonly=True)
    try:
        ib = client.ib
        logger.info("Connected=%s", ib.isConnected())

        accounts = ib.managedAccounts()
        logger.info("Accounts seen by API: %s", accounts)
        account = IB_ACCOUNT or (accounts[0] if accounts else None)
        if not account:
            raise RuntimeError("No account specified or returned by IB.")

        positions = ib.reqPositions()
        if positions:
            logger.info("Positions (%d):", len(positions))
            for pos in positions:
                contract = pos.contract
                logger.info(
                    "Account=%s Symbol=%s SecType=%s Qty=%s AvgCost=%s",
                    pos.account,
                    contract.symbol,
                    contract.secType,
                    pos.position,
                    pos.avgCost,
                )
        else:
            logger.info("Positions: []")

        logger.info("Subscribing to PnL for account=%s", account)
        pnl = ib.reqPnL(account)
        ib.sleep(2.0)
        logger.info(
            "Daily=%s Unrealized=%s Realized=%s",
            pnl.dailyPnL,
            pnl.unrealizedPnL,
            pnl.realizedPnL,
        )
        try:
            ib.cancelPnL(pnl)
        except TypeError:
            logger.warning("cancelPnL failed due to unhashable key; ignoring.")
    finally:
        client.disconnect()
        logger.info("Disconnected.")


def main() -> None:
    print_positions_and_pnl()


if __name__ == "__main__":
    main()

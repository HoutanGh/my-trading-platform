import os

from dotenv import load_dotenv
from loguru import logger

from apps.ib.client import IBClient


load_dotenv()

IB_HOST = os.getenv("IB_HOST", "172.21.224.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "2001"))


def print_positions() -> None:
    """Connect to IB and print out current positions."""
    logger.info(
        "Connecting to IB host=%s port=%s clientId=%s", IB_HOST, IB_PORT, IB_CLIENT_ID
    )
    client = IBClient()
    client.connect(IB_HOST, IB_PORT, IB_CLIENT_ID, readonly=True)
    try:
        ib = client.ib
        logger.info("Connected=%s", ib.isConnected())
        positions = ib.positions()
        if not positions:
            logger.info("No positions returned.")
        else:
            logger.info("Received %d positions", len(positions))
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
    finally:
        client.disconnect()
        logger.info("Disconnected.")


def main() -> None:
    print_positions()


if __name__ == "__main__":
    main()


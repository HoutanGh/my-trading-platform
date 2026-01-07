import asyncio

from dotenv import load_dotenv

from appsv2.adapters.broker.ibkr_connection import (
    IBKRConnection,
    IBKRConnectionConfig,
)
from appsv2.cli.repl import REPL


def main() -> None:
    load_dotenv()
    config = IBKRConnectionConfig.from_env()
    repl = REPL(IBKRConnection(config))
    asyncio.run(repl.run())


if __name__ == "__main__":
    main()

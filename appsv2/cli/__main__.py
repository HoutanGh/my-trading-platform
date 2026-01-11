import asyncio
import os

from dotenv import load_dotenv

from appsv2.adapters.broker.ibkr_connection import (
    IBKRConnection,
    IBKRConnectionConfig,
)
from appsv2.adapters.broker.ibkr_order_port import IBKROrderPort
from appsv2.adapters.eventbus.in_process import InProcessEventBus
from appsv2.adapters.logging.jsonl_logger import JsonlEventLogger
from appsv2.adapters.market_data.ibkr_bars import IBKRBarStream
from appsv2.adapters.pnl.flex_ingest import FlexCsvPnlIngestor
from appsv2.adapters.pnl.store import PostgresDailyPnlStore
from appsv2.cli.event_printer import make_prompting_event_printer
from appsv2.cli.order_tracker import OrderTracker
from appsv2.cli.repl import REPL
from appsv2.core.orders.service import OrderService
from appsv2.core.pnl.service import PnlService


def main() -> None:
    load_dotenv()
    config = IBKRConnectionConfig.from_env()
    bus = InProcessEventBus()
    prompt = "appsv2> "
    bus.subscribe(object, make_prompting_event_printer(prompt))
    log_path = os.getenv("APPV2_EVENT_LOG_PATH", "appsv2/journal/events.jsonl")
    if log_path:
        bus.subscribe(object, JsonlEventLogger(log_path).handle)
    connection = IBKRConnection(config)
    bar_stream = IBKRBarStream(connection)
    order_port = IBKROrderPort(connection, event_bus=bus)
    order_service = OrderService(order_port, event_bus=bus)
    order_tracker = OrderTracker()
    bus.subscribe(object, order_tracker.handle_event)
    pnl_store = PostgresDailyPnlStore()
    pnl_ingestor = FlexCsvPnlIngestor(pnl_store)
    pnl_service = PnlService(pnl_ingestor, pnl_store, event_bus=bus)
    repl = REPL(
        connection,
        order_service,
        order_tracker,
        pnl_service=pnl_service,
        bar_stream=bar_stream,
        event_bus=bus,
        prompt=prompt,
    )
    asyncio.run(repl.run())


if __name__ == "__main__":
    main()

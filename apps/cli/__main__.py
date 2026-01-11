import asyncio
import os

from dotenv import load_dotenv

from apps.adapters.broker.ibkr_connection import (
    IBKRConnection,
    IBKRConnectionConfig,
)
from apps.adapters.broker.ibkr_order_port import IBKROrderPort
from apps.adapters.eventbus.in_process import InProcessEventBus
from apps.adapters.logging.jsonl_logger import JsonlEventLogger
from apps.adapters.market_data.ibkr_bars import IBKRBarStream
from apps.adapters.pnl.flex_ingest import FlexCsvPnlIngestor
from apps.adapters.pnl.store import PostgresDailyPnlStore
from apps.cli.event_printer import make_prompting_event_printer
from apps.cli.order_tracker import OrderTracker
from apps.cli.repl import REPL
from apps.core.orders.service import OrderService
from apps.core.pnl.service import PnlService


def main() -> None:
    load_dotenv()
    config = IBKRConnectionConfig.from_env()
    bus = InProcessEventBus()
    prompt = "apps> "
    bus.subscribe(object, make_prompting_event_printer(prompt))
    log_path = os.getenv("APPS_EVENT_LOG_PATH")
    if log_path is None:
        log_path = os.getenv("APPV2_EVENT_LOG_PATH", "apps/journal/events.jsonl")
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

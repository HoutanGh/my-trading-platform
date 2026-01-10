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
from appsv2.cli.event_printer import print_event
from appsv2.cli.order_tracker import OrderTracker
from appsv2.cli.repl import REPL
from appsv2.core.orders.service import OrderService


def main() -> None:
    load_dotenv()
    config = IBKRConnectionConfig.from_env()
    bus = InProcessEventBus()
    bus.subscribe(object, print_event)
    log_path = os.getenv("APPV2_EVENT_LOG_PATH", "appsv2/journal/events.jsonl")
    if log_path:
        bus.subscribe(object, JsonlEventLogger(log_path).handle)
    connection = IBKRConnection(config)
    order_port = IBKROrderPort(connection, event_bus=bus)
    order_service = OrderService(order_port, event_bus=bus)
    order_tracker = OrderTracker()
    bus.subscribe(object, order_tracker.handle_event)
    repl = REPL(connection, order_service, order_tracker)
    asyncio.run(repl.run())


if __name__ == "__main__":
    main()

import asyncio
import os

from dotenv import load_dotenv

from apps.adapters.broker.ibkr_connection import (
    IBKRConnection,
    IBKRConnectionConfig,
)
from apps.adapters.broker.ibkr_order_port import IBKROrderPort
from apps.adapters.broker.ibkr_positions_port import IBKRPositionsPort
from apps.adapters.eventbus.in_process import InProcessEventBus
from apps.adapters.logging.jsonl_logger import JsonlEventLogger
from apps.adapters.logging.ib_gateway_tail import tail_ib_gateway_log
from apps.adapters.market_data.ibkr_bars import IBKRBarStream
from apps.adapters.market_data.ibkr_quotes import IBKRQuoteSnapshot
from apps.adapters.pnl.flex_ingest import FlexCsvPnlIngestor
from apps.adapters.pnl.store import PostgresDailyPnlStore
from apps.cli.event_printer import make_prompting_event_printer
from apps.cli.order_tracker import OrderTracker
from apps.cli.position_origin_tracker import PositionOriginTracker
from apps.cli.repl import REPL
from apps.core.orders.service import OrderService
from apps.core.pnl.service import PnlService
from apps.core.positions.service import PositionsService
from apps.core.ops.events import IbGatewayRawLine


def _fanout_logger(*loggers):
    def _log(event: object) -> None:
        for logger in loggers:
            if logger:
                logger(event)

    return _log


async def _run_ib_gateway_tail(
    source_path: str,
    *,
    logger,
    poll_interval: float,
    start_at_end: bool,
) -> None:
    async for line in tail_ib_gateway_log(
        source_path,
        poll_interval=poll_interval,
        start_at_end=start_at_end,
    ):
        logger(IbGatewayRawLine.now(line=line, source_path=source_path))


async def _async_main() -> None:
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
    ops_log_path = os.getenv("APPS_OPS_LOG_PATH", "apps/journal/ops.jsonl")
    ops_logger = JsonlEventLogger(ops_log_path).handle if ops_log_path else None
    ib_log_path = os.getenv("APPS_IB_GATEWAY_LOG_PATH", "apps/journal/ib_gateway.jsonl")
    ib_logger = JsonlEventLogger(ib_log_path).handle if ib_log_path else None
    event_logger = _fanout_logger(bus.publish)
    gateway_logger = _fanout_logger(ib_logger, bus.publish)
    connection = IBKRConnection(config, gateway_logger=gateway_logger, event_logger=event_logger)
    bar_stream = IBKRBarStream(connection, event_logger=event_logger)
    quote_port = IBKRQuoteSnapshot(connection)
    order_port = IBKROrderPort(connection, event_bus=bus)
    order_service = OrderService(order_port, event_bus=bus)
    order_tracker = OrderTracker()
    bus.subscribe(object, order_tracker.handle_event)
    position_origin_tracker = PositionOriginTracker(log_path=log_path)
    bus.subscribe(object, position_origin_tracker.handle_event)
    positions_port = IBKRPositionsPort(connection)
    positions_service = PositionsService(positions_port)
    pnl_store = PostgresDailyPnlStore()
    pnl_ingestor = FlexCsvPnlIngestor(pnl_store)
    pnl_service = PnlService(pnl_ingestor, pnl_store, event_bus=bus)
    initial_config: dict[str, str] = {}
    account_defaults: dict[str, str] = {}
    paper_account = os.getenv("IB_PAPER_ACCOUNT")
    live_account = os.getenv("IB_LIVE_ACCOUNT")
    fallback_account = os.getenv("IB_ACCOUNT")
    if paper_account:
        account_defaults["paper"] = paper_account
    if live_account:
        account_defaults["live"] = live_account
    if fallback_account:
        account_defaults.setdefault("paper", fallback_account)
        account_defaults.setdefault("live", fallback_account)
    if account_defaults.get("paper"):
        initial_config["account"] = account_defaults["paper"]
    repl = REPL(
        connection,
        order_service,
        order_tracker,
        pnl_service=pnl_service,
        positions_service=positions_service,
        position_origin_tracker=position_origin_tracker,
        bar_stream=bar_stream,
        quote_port=quote_port,
        event_bus=bus,
        ops_logger=ops_logger,
        prompt=prompt,
        initial_config=initial_config,
        account_defaults=account_defaults,
    )
    ib_gateway_source = os.getenv("IB_GATEWAY_LOG_PATH")
    ib_gateway_raw_log_path = os.getenv(
        "APPS_IB_GATEWAY_RAW_LOG_PATH",
        "apps/journal/ib_gateway_raw.jsonl",
    )
    tail_task = None
    if ib_gateway_source and ib_gateway_raw_log_path:
        tail_logger = JsonlEventLogger(ib_gateway_raw_log_path).handle
        tail_task = asyncio.create_task(
            _run_ib_gateway_tail(
                ib_gateway_source,
                logger=tail_logger,
                poll_interval=float(os.getenv("IB_GATEWAY_LOG_POLL_SECS", "0.5")),
                start_at_end=os.getenv("IB_GATEWAY_LOG_FROM_START", "0") == "1",
            )
        )
    try:
        await repl.run()
    finally:
        if tail_task:
            tail_task.cancel()
            await asyncio.gather(tail_task, return_exceptions=True)


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()

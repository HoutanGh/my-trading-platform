# My Trading Platform (appsv2)

This repo centers on appsv2 (core + adapters + CLI + API) with a separate web UI.
The legacy `apps/` folder is no longer used by appsv2.

## appsv2 goals

- Keep core trading logic independent of IBKR and the CLI.
- Use a small in-process event bus so the CLI can react to events without tight coupling.
- Make the CLI a thin, async REPL that stays responsive while work happens in the background.

## Directory map (appsv2)

```
appsv2/
  core/
    orders/
      models.py     # OrderSpec, OrderAck, enums
      ports.py      # OrderPort + EventBus interface
      service.py    # OrderService (validation + intent)
      events.py     # OrderIntent, OrderSent, OrderIdAssigned, OrderStatusChanged
    pnl/
      models.py     # DailyPnlRow, PnlIngestResult
      ports.py      # DailyPnlStore + FlexPnlIngestor interfaces
      service.py    # PnlService (ingest + query)
      events.py     # PnlIngestStarted/Finished/Failed
  adapters/
    broker/
      ibkr_connection.py  # connect/disconnect + config
      ibkr_order_port.py  # OrderPort implementation (IBKR)
    pnl/
      db.py              # Postgres connection + schema
      store.py           # daily_pnl upserts + queries
      flex_ingest.py     # CSV parsing + aggregation
    eventbus/
      in_process.py       # in-process event bus
    logging/
      jsonl_logger.py     # JSONL event logger (subscriber)
  api/
    main.py               # FastAPI entrypoint
    routes/
      pnl.py              # /pnl/daily endpoint
  cli/
    __main__.py     # CLI entrypoint + wiring
    repl.py          # async REPL + commands
    event_printer.py # prints events to console
    order_tracker.py # in-memory order status cache
```

## Core concepts

- OrderSpec: broker-agnostic order contract (symbol, qty, side, type, limit, etc).
- OrderService: validates OrderSpec, publishes OrderIntent, and calls OrderPort.
- OrderPort: interface for submitting orders (implemented by IBKROrderPort).
- EventBus: interface for publishing/subscribing to events.
- Events: simple dataclasses used for runtime observability.

## Flow (basic buy)

1) CLI parses a command and builds an OrderSpec.
2) OrderService validates and publishes OrderIntent.
3) IBKROrderPort submits to IBKR and publishes:
   - OrderSent (placeOrder called)
   - OrderIdAssigned (order id known)
   - OrderStatusChanged (first status snapshot)
4) CLI prints events, writes JSONL logs, and updates the order tracker.

In short:

CLI -> OrderService -> OrderPort(IBKR) -> IBKR
               |            |
               |            +-> events
               +-> events

## Event meanings (be precise)

- OrderIntent: order validated, about to be sent to broker.
- OrderSent: placeOrder(...) was called.
- OrderIdAssigned: broker assigned an order_id.
- OrderStatusChanged: status snapshot/update from broker.

## CLI usage (appsv2)

Launch:

```
python -m appsv2.cli
```

Commands (current):

- connect [paper|live] [--host ... --port ... -c ...]
- status
- buy SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false]
- sell SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false]
- orders [pending]
- ingest-flex csv=... account=... [source=flex]
- set key=value [key=value ...]
- show config
- disconnect
- quit

Flags are accepted in three styles:

- key=value
- --long value or --long=value
- -s short

Examples:

```
connect paper --host 127.0.0.1 --port 7497 -c 1001
set symbol=AAPL qty=5 tif=DAY
buy AAPL qty=5 limit=189.50
sell --symbol TSLA -q 2 -l 242.10
orders --pending
ingest-flex csv=data/raw/Daily_PL.csv account=DU123456
```

Notes:

- orders shows the in-memory tracker fed by events (no broker query yet).
- buy/sell default symbol/qty/tif/etc from `set` if you omit them.
- ingest-flex reads a Flex CSV, aggregates daily P&L, and upserts into Postgres.

## PnL ingestion + calendar

High-level flow:

CSV -> CLI ingest-flex -> PnL service -> CSV + DB adapters -> Postgres daily_pnl
Postgres daily_pnl -> API /pnl/daily -> web calendar

Quick start:

1) Start Postgres and set `DATABASE_URL`.
2) Ingest a CSV:
   - `python -m appsv2.cli`
   - `ingest-flex csv=data/raw/Daily_PL.csv account=DU123456`
3) Start the API:
   - `uvicorn appsv2.api.main:app --reload`
4) Start the web UI:
   - `cd web && npm run dev`

## Logging

Events are logged to JSONL by default:

- APPV2_EVENT_LOG_PATH (default: appsv2/journal/events.jsonl)

Each line contains the event type and serialized event payload.

## Glossary

- IBKR: Interactive Brokers.
- TIF: Time in force (DAY, GTC).
- RTH: Regular Trading Hours (outside_rth enables extended hours).
- OCA: One-Cancels-All (not used in appsv2 yet).
- Port: interface defined in core (OrderPort, EventBus).
- Adapter: implementation of a port (IBKR adapter, event bus).
- Event bus: in-process pub/sub that lets subscribers react to events.
- OrderSpec: broker-agnostic order request.
- OrderAck: submit-time acknowledgement (order_id + status snapshot).

## Other parts of the repo (legacy)

- apps/: legacy code, no longer used by appsv2.
- web/: React calendar for daily realized PnL (read-only UI).
- docs/: design notes.

If you are working on appsv2, the v1 code is reference only and not part of the current architecture.

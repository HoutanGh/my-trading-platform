# My Trading Platform (apps)

This repo centers on apps (core + adapters + CLI + API) with a separate web UI.
It includes a production-ready breakout automation flow in apps.
The legacy `apps_archive/` folder is no longer used by the `apps/` package.

## Summary

This platform runs real-time, rule-based trading workflows with a clean architecture that separates decision-making from external integrations: the `core` layer holds the trading logic and domain rules, `adapters` connect that logic to systems like IBKR and Postgres, and the CLI/API provide practical control surfaces for operators and reporting; together, this design keeps behavior consistent, observable, and easier to evolve safely.

## apps goals

- Keep core trading logic independent of IBKR and the CLI.
- Use a small in-process event bus so the CLI can react to events without tight coupling.
- Make the CLI a thin, async REPL that stays responsive while work happens in the background.

## Directory map (apps)

```
apps/
  core/
    market_data/
      models.py     # Bar model
      ports.py      # BarStreamPort interface
    orders/
      models.py     # OrderSpec, OrderAck, enums
      ports.py      # OrderPort + EventBus interface
      service.py    # OrderService (validation + intent)
      events.py     # OrderIntent, OrderSent, OrderIdAssigned, OrderStatusChanged
    strategies/
      breakout/
        logic.py    # Breakout rule + state
        runner.py   # Breakout watcher (bar stream -> order)
        events.py   # Breakout lifecycle events
    pnl/
      models.py     # DailyPnlRow, PnlIngestResult
      ports.py      # DailyPnlStore + FlexPnlIngestor interfaces
      service.py    # PnlService (ingest + query)
      events.py     # PnlIngestStarted/Finished/Failed
  adapters/
    broker/
      ibkr_connection.py  # connect/disconnect + config
      ibkr_order_port.py  # OrderPort implementation (IBKR)
    market_data/
      ibkr_bar_stream.py    # BarStreamPort implementation (IBKR)
      ibkr_bar_history.py   # BarHistoryPort implementation (IBKR)
      ibkr_quote_stream.py  # QuoteStreamPort implementation (IBKR)
      ibkr_quote_snapshot.py # QuotePort implementation (IBKR)
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
- BracketOrderSpec: entry + TP/SL bracket contract.
- OrderService: validates OrderSpec, publishes OrderIntent, and calls OrderPort.
- OrderPort: interface for submitting orders (implemented by IBKROrderPort).
- BarStreamPort: interface for streaming live bars.
- Breakout runner: ties bar stream to breakout logic and order submission.
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

## Flow (breakout automation)

1) CLI starts a breakout watcher with symbol/level/qty (optional TP/SL).
2) Bar stream emits 1-minute bars from IBKR.
3) Breakout logic evaluates each bar for break/confirm.
4) On confirm:
   - submit market entry, or
   - submit bracket order when TP/SL are provided.
5) Breakout lifecycle events are emitted and the watcher stops.

## Event meanings (be precise)

- OrderIntent: order validated, about to be sent to broker.
- OrderSent: placeOrder(...) was called.
- OrderIdAssigned: broker assigned an order_id.
- OrderStatusChanged: status snapshot/update from broker.
- BreakoutStarted/BreakoutBreakDetected/BreakoutConfirmed/BreakoutStopped: breakout lifecycle milestones.

## Bar Stream Health + Recovery

- IB gateway connectivity and per-symbol bar-subscription health are tracked separately.
- Health events include:
  - `BarStreamStalled`, `BarStreamRecovered`
  - `BarStreamHeal` (`BarStreamRecoveryStarted`), `BarStreamHealFail` (`BarStreamRecoveryFailed`)
  - `BarStreamBlocked` / `BarStreamUnblocked` for IB code `10197` (competing session)
  - `BarStreamScan` when a global recovery pass is scheduled
- Recovery grouping is by symbol + RTH mode, so slow/fast breakout streams for the same symbol are recovered together.

Environment flags:
- `APPS_BAR_SELF_HEAL_ENABLED` (default `false`, monitor-only by default)
- `APPS_BAR_HEALTH_POLL_SECS` (default `1.0`)
- `APPS_BAR_RECOVERY_COOLDOWN_SECS` (default `5.0`)
- `APPS_BAR_RECOVERY_MAX_ATTEMPTS` (default `5`)
- `APPS_BAR_RECOVERY_MAX_CONCURRENCY` (default `1`)
- `APPS_BAR_RECOVERY_STALL_BURST_COUNT` (default `2`)

## CLI usage (apps)

Launch:

```
python -m apps.cli
```

Commands (current):

- connect [paper|live] [--host ... --port ... -c ...]
- status
- buy SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false]
- sell SYMBOL qty=... [limit=...] [tif=DAY] [outside_rth=true|false]
- can-trade SYMBOL [SYMBOL ...] [side=BUY|SELL] [qty=1] [tif=DAY] [outside_rth=true|false] [exchange=SMART] [currency=USD] [account=...]
- breakout SYMBOL level=... qty=... [tp=...] [sl=...] [rth=true|false] [bar=1 min] [max_bars=...]
- orders [pending]
- positions [account=...]
- positions realized [account=...] [sort=asc|desc] [min=...]
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
can-trade AAPL TSLA side=BUY qty=1
breakout AAPL level=190 qty=1 tp=195 sl=187
orders --pending
ingest-flex csv=data/raw/Daily_PL.csv account=DU123456
```

Notes:

- orders shows the in-memory tracker fed by events (no broker query yet).
- buy/sell default symbol/qty/tif/etc from `set` if you omit them.
- can-trade runs an IBKR what-if check and reports whether the broker accepts the symbol for the requested side/account/session settings.
- ingest-flex reads a Flex CSV, aggregates daily P&L, and upserts into Postgres.

## PnL ingestion + calendar

High-level flow:

CSV -> CLI ingest-flex -> PnL service -> CSV + DB adapters -> Postgres daily_pnl
Postgres daily_pnl -> API /pnl/daily -> web calendar

Quick start:

1) Start Postgres and set `DATABASE_URL`.
2) Ingest a CSV:
   - `python -m apps.cli`
   - `ingest-flex csv=data/raw/Daily_PL.csv account=DU123456`
3) Start the API:
   - `uvicorn apps.api.main:app --reload`
4) Start the web UI:
   - `cd web && npm run dev`

## Logging

Events are logged to JSONL by default:

- APPS_EVENT_LOG_PATH (default: apps/journal/events.jsonl; legacy: APPV2_EVENT_LOG_PATH)

Each line contains the event type and serialized event payload.

## Glossary

- IBKR: Interactive Brokers.
- TIF: Time in force (DAY, GTC).
- RTH: Regular Trading Hours (outside_rth enables extended hours).
- OCA: One-Cancels-All (used for bracket TP/SL).
- Bracket order: entry plus TP/SL exits linked by OCA.
- Port: interface defined in core (OrderPort, EventBus).
- Adapter: implementation of a port (IBKR adapter, event bus).
- Event bus: in-process pub/sub that lets subscribers react to events.
- OrderSpec: broker-agnostic order request.
- OrderAck: submit-time acknowledgement (order_id + status snapshot).

## Other parts of the repo (legacy)

- apps_archive/: legacy code, no longer used by the `apps/` package.
- web/: React calendar for daily realized PnL (read-only UI).
- docs/: design notes.

If you are working on apps, the v1 code in `apps_archive/` is reference only and not part of the current architecture.

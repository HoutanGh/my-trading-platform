# Breakout Automation - Production Architecture (apps)

This document describes the production-ready architecture for the breakout automation as implemented in apps. It focuses on system responsibilities, data flow, and operational behavior. It is intended to be high-signal context for future Codex sessions.

---
.
├── README.md
├── docs/
│   └── breakout_automation.md
└── apps/
    ├── cli/
    │   ├── __main__.py
    │   ├── repl.py
    │   ├── event_printer.py
    │   ├── order_tracker.py
    │   └── position_origin_tracker.py
    ├── core/
    │   ├── market_data/
    │   │   ├── models.py
    │   │   └── ports.py
    │   ├── orders/
    │   │   ├── events.py
    │   │   ├── models.py
    │   │   ├── ports.py
    │   │   └── service.py
    │   └── strategies/
    │       └── breakout/
    │           ├── __init__.py
    │           ├── events.py
    │           ├── logic.py
    │           └── runner.py
    └── adapters/
        ├── broker/
        │   ├── ibkr_connection.py
        │   └── ibkr_order_port.py
        ├── eventbus/
        │   └── in_process.py
        ├── logging/
        │   └── jsonl_logger.py
        └── market_data/
            ├── ibkr_quote_stream.py
            └── ibkr_bars.py


## 1. What the system does (current)

- Accepts a breakout configuration from the CLI (symbol, level, qty, optional TP/SL).
- Streams 1-minute market data from IBKR.
- Optionally streams 1-second bars for a fast-entry override.
- Applies a simple breakout rule: **enter when a bar closes at or above the level**.
- On entry, submits either:
  - a **limit entry at the ask** by default, or
  - a bracket order with TP/SL if provided (entry type matches the breakout entry type).
- Emits events for visibility and audit.
- Stops after one trade attempt (single-fire).

---

## 2. User workflow (CLI)

Operator runs the apps CLI and starts a breakout watcher:

- Example (market entry, key/value):
  - `breakout AAPL level=190 qty=1`
- Example (market entry + TP/SL bracket, key/value):
  - `breakout AAPL level=190 qty=1 tp=195 sl=187`
- Example (positional):
  - `breakout AAPL 190 1 195 187`

Stop or check status:
- `breakout status`
- `breakout stop AAPL`

Quick orders:
- `buy AAPL 100`
- `sell AAPL 50`
 - `orders cancel 12345`
 - `orders replace 12345 limit=191.25`

---

## 3. Components and responsibilities

### CLI and orchestration
- `apps/cli/__main__.py`
  - Wires the event bus, IBKR connection, order service, bar stream adapter, and REPL.
- `apps/cli/repl.py`
  - Parses `breakout` commands, validates input, and starts/stops watcher tasks.

### Strategy (unique logic)
- `apps/core/strategies/breakout/logic.py`
  - Pure breakout rule and state machine. This is the only strategy-specific logic.
- `apps/core/strategies/breakout/runner.py`
  - Runs the strategy against the live bar stream and submits orders.
- `apps/core/strategies/breakout/events.py`
  - Emits lifecycle events: started, break detected, confirmed, rejected, stopped.

### Market data (reusable)
- `apps/core/market_data/models.py`
  - Defines the `Bar` model.
- `apps/core/market_data/ports.py`
  - Defines the `BarStreamPort`, `QuotePort`, and `QuoteStreamPort` interfaces.
- `apps/adapters/market_data/ibkr_bars.py`
  - IBKR implementation of the bar stream (1-minute bars, live updates).
  - Important: on each new bar, the adapter emits the **previous bar** (the bar that just closed).
- `apps/adapters/market_data/ibkr_quote_stream.py`
  - IBKR streaming quote cache (reqMktData) with ref-counted subscriptions.
- `apps/adapters/market_data/ibkr_quotes.py`
  - IBKR snapshot quote adapter used as a fallback.

### Orders (reusable)
- `apps/core/orders/service.py`
  - Validates and submits orders; emits order lifecycle events.
- `apps/core/orders/models.py`
  - `OrderSpec` for standard orders and `BracketOrderSpec` for TP/SL.
- `apps/adapters/broker/ibkr_order_port.py`
  - Converts order specs to IBKR orders (including bracket/OCA logic).
  - Supports cancelling and replacing basic limit orders from the current session.

### Events and logging (reusable)
- `apps/adapters/eventbus/in_process.py`
  - In-process event bus for pub/sub.
- `apps/cli/event_printer.py`
  - Prints events to the console.
- `apps/adapters/logging/jsonl_logger.py`
  - Writes event payloads to JSONL for audit.

---

## 4. Runtime sequence

1. Operator starts the breakout watcher via CLI.
2. Runner subscribes to 1-minute bars from IBKR.
3. If fast entry is enabled, runner also subscribes to 1-second bars.
4. Runner starts a streaming quote subscription (reqMktData) for the symbol and caches latest bid/ask.
5. Each **closed** 1-minute bar is fed into the breakout logic.
6. If bar close >= level:
   - If TP/SL provided: submit bracket order (parent + TP + SL, OCA).
   - Otherwise: submit a single market entry.
   - For limit entries, use the cached streaming quote if it is fresh; otherwise warm up briefly, then fall back to a snapshot quote once.
7. If fast entry fires first (1-second high beyond a time-decayed distance, close above level, and spread proxy passes):
   - Submit the entry immediately.
   - Bypass the 1-minute close entry (single-fire).
8. Order lifecycle events are emitted and logged.
9. Runner stops (single-fire).

---

## 5. Order behavior

- Limit entry at the ask is default.
- Market entry is available via explicit config.
- If `tp` and `sl` are provided, a bracket order is placed:
  - Parent: entry BUY (limit at ask by default, or market if configured).
  - Children: TP limit SELL and SL stop-limit SELL (0.02 offset).
  - Children are linked with an OCA group so one cancels the other.
  - IBKR enforces TP/SL even if the app disconnects after submission.
- If `tp` is a ladder (e.g., `tp=1.1-1.3-1.5`) with `sl`:
  - Parent: entry BUY (limit at ask by default, or market if configured).
  - Children: multiple TP limit SELL orders (split by default ratios).
  - Stop: a stop-limit SELL for the remaining qty (outside RTH).
  - Stop is bumped after TP1 to the breakout level, and after TP2 to TP1.
  - Remaining exits are app-managed (not broker OCA) after fills.
  
Notes:
- Market orders can fill very quickly (especially in paper), which can look "instant" after bar close.
- Bracket child status/fill events are emitted for visibility.

---

## 6. Observability and audit

Events emitted (full audit log):
- Breakout lifecycle: Started, BreakDetected, FastTriggered, Confirmed, Rejected, Stopped.
- Order lifecycle: Intent, Sent, OrderIdAssigned, StatusChanged, Filled.
- Bracket children: child status/filled events for TP/SL.

Logging:
- Console output via `event_printer` (intentionally filtered to reduce noise, timestamps include timezone offset).
- JSONL audit log via `jsonl_logger` (path controlled by `APPS_EVENT_LOG_PATH`), which includes **all** events.
  - Includes IBKR connection lifecycle and bar stream health events (e.g., connect attempts, gaps, lag).
  - BreakoutRejected includes quote age/max when the rejection reason is `quote_stale`.
- CLI error log via `APPS_OPS_LOG_PATH` (default `apps/journal/ops.jsonl`).
- IBKR gateway/API error log via `APPS_IB_GATEWAY_LOG_PATH` (default `apps/journal/ib_gateway.jsonl`).
- IBKR Gateway/TWS file tail log via `IB_GATEWAY_LOG_PATH` (source) -> `APPS_IB_GATEWAY_RAW_LOG_PATH` (default `apps/journal/ib_gateway_raw.jsonl`).
  - Optional: `IB_GATEWAY_LOG_POLL_SECS` (default 0.5) and `IB_GATEWAY_LOG_FROM_START=1` to backfill from the beginning.

---

## 7. Operational behavior and guardrails

- Single-fire: each watcher stops after one trade attempt.
- Requires active IBKR connection; otherwise the CLI refuses to start a watcher.
- If the bar stream is canceled or the watcher is stopped, the stream is cleaned up.
- Paper-only guard is enforced by connection config (paper vs live ports).
- If the breakout uses limit-at-ask and a valid ask quote is missing or stale, the watcher rejects and stops.
  - The quote freshness guard uses a max-age threshold (default 2s) and optional warmup; stale quotes are rejected to prevent using outdated prices.

---

## 8. Configuration inputs

CLI fields:
- Required: `symbol`, `level`, `qty`
- Optional: `tp`, `sl`, `rth`, `bar`, `fast`, `fast_bar`, `max_bars`, `tif`, `outside_rth`, `entry`, `quote_age`, `account`, `client_tag`

Defaults:
- If `rth` is false (default), `outside_rth` defaults to true for breakout orders so pre/postmarket entries are allowed.

Environment (IBKR):
- `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `PAPER_ONLY`, and related paper/live port settings.

Fast entry defaults (enabled by default):
- Uses 1-second **high** for distance checks and 1-second **close** for acceptance.
- 1-second bar size default is `1 secs` (IBKR bar size string).
- Distance decays linearly every 10s from 15¢ to 5¢ (rounded to whole cents).
- Price regime scaling based on breakout level: `<$1` uses $1–$4 rules, `$1–$4` = 1.0×, `$4–$7` = 1.1×, `$7–$10` = 1.25×, `>$10` uses $7–$10 rules.
- Spread proxy uses 1-second high–low range with a max that decays from 4¢ to 1¢ (rounded to whole cents).
- Minute boundaries are inferred from 1-second bar timestamps when aligning the fast threshold schedule.

---

## 9. Limitations (current)

- No auto re-arm; each watcher is single-fire.
- Breakout rule is fixed (single bar close >= level triggers entry).
- Fast entry uses fixed, strategy-configured thresholds (time-decayed distance and 1-sec high-low proxy), not adaptive noise models.
- No volatility-based sizing or dynamic TP/SL yet.
 - Order replace only supports limit orders tracked by the current session.

---

## 10. Future enhancements (optional)

- Automated TP/SL from volatility or historical structure.
- Sizing off for profits or stop losses
- Entry types beyond market (limit/stop entry).
- Strategy re-arming and session scheduling.
- Real-time PnL tracking and fill-based performance stats.

---

## 11. Gotchas + debugging notes (recent)

- IBKR bar timestamps are **bar start** times. The bar is only emitted to the strategy when it closes.
- If you start mid-bar, the first bar you see is the **closing bar you were already inside**.
- If price seems to "buy instantly," it is usually because the **first closed bar** already closed above the level and the entry is a market order.
- Console output is filtered; if you need order status detail, check `apps/journal/events.jsonl`.
- TP/SL visibility: CLI `positions` shows configured TP/SL if the breakout watcher included them.

# Breakout Automation - Production Architecture (apps)

This document describes the production breakout automation architecture in `apps/`, including the current TP ladder and TP reprice behavior.

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
    │   ├── analytics/
    │   │   └── flow/
    │   │       └── take_profit/
    │   │           ├── service.py
    │   │           └── calculator.py
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
            ├── ibkr_bar_history.py
            ├── ibkr_bar_stream.py
            ├── ibkr_quote_stream.py
            └── ibkr_quote_snapshot.py


## 1. What the system does (current)

- Accepts a breakout configuration from CLI (symbol, level, qty, optional TP/SL).
- Streams 1-minute bars from IBKR; optionally 1-second bars for fast entry.
- Applies breakout rule: enter when bar close is at/above level.
- Supports:
  - manual TP/SL (`tp=...`, `tp=1.1-1.3`, `sl=...`)
  - auto TP ladder (`tp=auto tp_count=2|3` or shorthand `tp-2` / `tp-3`).
- Auto TP ladder uses historical bars to produce fallback TP levels at watcher start.
- For auto ladder mode, after full entry fill the app can recalc TP levels from fill price and attempt to replace remaining TP child orders.
- Emits strategy/order events for audit and CLI visibility.
- Breakout watcher remains single-fire (one trade attempt), while TP reprice handling is coordinated in CLI event flow after submission.

---

## 2. User workflow (CLI)

Operator runs apps CLI and starts a watcher.

Examples:
- Basic breakout:
  - `breakout AAPL level=190 qty=1`
- Manual bracket:
  - `breakout AAPL level=190 qty=1 tp=195 sl=187`
- Manual ladder:
  - `breakout AAPL level=190 qty=100 tp=195-198-205 sl=187`
- Auto ladder (key/value):
  - `breakout XRTX level=0.42 qty=1000 tp=auto tp_count=2 sl=0.37`
- Auto ladder shorthand:
  - `breakout XRTX 0.42 1000 tp-2 sl=0.37`
- Auto ladder shorthand with custom split:
  - `breakout XRTX 0.42 1000 tp-2 sl=0.37 tp_alloc=85-15`

Stop or check status:
- `breakout status`
- `breakout cancel XRTX`
- `breakout cancel XRTX CISS`
- `breakout cancel ALL`

Quick orders:
- `buy AAPL 100`
- `sell AAPL 50`
- `orders cancel 12345`
- `orders replace 12345 limit=191.25`

---

## 3. Components and responsibilities

### CLI and orchestration
- `apps/cli/__main__.py`
  - Wires event bus, IBKR adapters, services, and REPL.
- `apps/cli/repl.py`
  - Parses breakout commands, resolves TP mode, computes initial auto TP levels, starts/stops watcher tasks.
  - Hosts TP reprice coordinator (event-driven, post-fill).
- `apps/cli/position_origin_tracker.py`
  - Tracks position origin tag and latest TP/SL display state (including TP updates).
- `apps/cli/event_printer.py`
  - Prints selected strategy/order events, including TP update events.

### Strategy (unique logic)
- `apps/core/strategies/breakout/logic.py`
  - Pure breakout and fast-entry threshold logic.
- `apps/core/strategies/breakout/runner.py`
  - Executes streaming breakout strategy and submits market/limit/bracket/ladder entries.
- `apps/core/strategies/breakout/events.py`
  - Breakout lifecycle events and TP update event (`BreakoutTakeProfitsUpdated`).

### Analytics (flow)
- `apps/core/analytics/flow/take_profit/calculator.py`
  - TP computation model (runner-oriented levels from bars).
- `apps/core/analytics/flow/take_profit/service.py`
  - Adaptive lookback + bar-size retry orchestration.
  - Supports explicit `anchor_price` (used for fill-based TP recalculation).

### Market data (reusable)
- `apps/core/market_data/models.py`
  - `Bar`, `Quote` models.
- `apps/core/market_data/ports.py`
  - `BarStreamPort`, `BarHistoryPort`, `QuotePort`, `QuoteStreamPort`.
- `apps/adapters/market_data/ibkr_bar_stream.py`
  - Streaming bars adapter.
- `apps/adapters/market_data/ibkr_bar_history.py`
  - Historical bars adapter.
- `apps/adapters/market_data/ibkr_quote_stream.py`
  - Streaming quote cache.
- `apps/adapters/market_data/ibkr_quote_snapshot.py`
  - Snapshot quote fallback.

### Orders (reusable)
- `apps/core/orders/service.py`
  - Validation + submit/replace/cancel interface.
- `apps/core/orders/models.py`
  - `OrderSpec`, `BracketOrderSpec`, `LadderOrderSpec`.
- `apps/adapters/broker/ibkr_order_port.py`
  - IBKR translation and child order event wiring.

### Events and logging
- `apps/adapters/eventbus/in_process.py`
  - In-process pub/sub.
- `apps/adapters/logging/jsonl_logger.py`
  - JSONL audit log sink.

---

## 4. Runtime sequence

1. Operator starts breakout via CLI.
2. REPL parses input and resolves TP mode.
3. If auto TP mode is requested:
   - REPL computes fallback TP ladder using `TakeProfitService` anchored at breakout level.
   - REPL sets TP qty split (defaults: `tp-2 => 80/20`, `tp-3 => 60/30/10`, override with `tp_alloc`).
4. Runner subscribes to slow bars (and optional fast bars) and streaming quotes.
5. On trigger, runner submits entry + exits:
   - bracket for single TP
   - ladder for 2/3 TP levels.
6. Runner stops (single-fire) after one trade attempt.
7. For auto ladder mode with reprice enabled:
   - REPL tracks order and child TP events by `client_tag`.
   - On full parent fill, REPL recalculates TP levels from `anchor_price=fill_price`.
   - REPL replaces TP child limit prices if no TP has filled and replace acks are accepted.
   - On full success, REPL publishes `BreakoutTakeProfitsUpdated`.

---

## 5. Order and TP behavior

- Default entry type: limit at ask.
- Market entry is supported via explicit `entry=market`.

Manual bracket:
- `tp` + `sl` with single TP price.
- Stop-loss uses stop-limit: initial limit equals `sl`; when the stop is elected, limit reprices to touch (`bid` for SELL, `ask` for BUY).

Manual ladder:
- `tp=price1-price2` or `tp=price1-price2-price3` + `sl`.
- Optional `tp_alloc=` supports custom quantity splits.
- Managed stop-loss uses stop-limit with the same trigger-time touch repricing behavior.

Auto ladder:
- `tp=auto tp_count=2|3` or `tp-2` / `tp-3` + `sl`.
- Initial TP ladder is computed at breakout start (fallback protection).
- Fill-time reprice tries to replace remaining TP levels using fill-anchored TP calculation.

V1 reprice safety rules:
- If any TP fill is detected before/during reprice, reprice is skipped.
- If child TP order IDs are unavailable by timeout, reprice is skipped.
- If any replace ack is not accepted, reprice is skipped and no TP-update success event is emitted.
- TP-update event is only published after all targeted TP child replacements are accepted.

---

## 6. Observability and audit

Emitted events (relevant groups):
- Breakout lifecycle: `BreakoutStarted`, `BreakoutBreakDetected`, `BreakoutFastTriggered`, `BreakoutConfirmed`, `BreakoutRejected`, `BreakoutStopped`.
- TP update: `BreakoutTakeProfitsUpdated`.
- Orders: `OrderIntent`, `OrderSent`, `OrderIdAssigned`, `OrderStatusChanged`, `OrderFilled`.
- Bracket/ladder children: `BracketChildOrderStatusChanged`, `BracketChildOrderFilled`.
- Bar stream health/recovery: `BarStreamStalled`, `BarStreamRecovered`, `BarStreamRecoveryStarted`, `BarStreamRecoveryFailed`, `BarStreamCompetingSessionBlocked`, `BarStreamCompetingSessionCleared`, `BarStreamRecoveryScanScheduled`.

Display behavior:
- `event_printer` shows `BreakoutTpUpdated` when TP update event is emitted.
- `positions` TP column uses latest tracked TP list when available (not only TP1).

Logging:
- JSONL via `APPS_EVENT_LOG_PATH` includes all emitted events.
- Ops and gateway logs unchanged (`APPS_OPS_LOG_PATH`, `APPS_IB_GATEWAY_LOG_PATH`, raw tail options).

---

## 7. Operational behavior and guardrails

- Breakout watchers are single-fire.
- Active IBKR connection required to start watchers.
- Paper/live guard behavior unchanged (paper defaults/ports).
- Limit-at-ask entry rejects on missing/stale quote.
- TP reprice is conservative by design:
  - prefers skip over forcing uncertain state transitions.

---

## 8. Configuration inputs

Core CLI fields:
- Required: `symbol`, `level`, `qty`
- Optional common: `tp`, `sl`, `rth`, `bar`, `fast`, `fast_bar`, `max_bars`, `tif`, `outside_rth`, `entry`, `quote_age`, `account`, `client_tag`

Auto TP / reprice fields:
- `tp=auto`
- `tp_count=1|2|3`
- shorthand token: `tp-1|tp-2|tp-3`
- `tp_alloc=...` (example: `80-20`, `60-30-10`)
- `tp_bar` / `tp_bar_size` (historical bar size for TP calculation)
- `tp_rth` / `tp_use_rth`
- `tp_timeout` (seconds for child TP ID readiness)

Defaults:
- If `rth=false`, breakout orders default `outside_rth=true`.
- Auto TP default allocation:
  - `tp-2 => 80/20`
  - `tp-3 => 60/30/10`

Environment:
- IBKR connection env vars unchanged (`IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `PAPER_ONLY`, etc.).
- Bar stream health/recovery:
  - `APPS_BAR_SELF_HEAL_ENABLED` (default `false`)
  - `APPS_BAR_HEALTH_POLL_SECS`
  - `APPS_BAR_STALL_MULTIPLIER`
  - `APPS_BAR_STALL_FLOOR_SECS`
  - `APPS_BAR_RECOVERY_COOLDOWN_SECS`
  - `APPS_BAR_RECOVERY_MAX_ATTEMPTS`
  - `APPS_BAR_RECOVERY_MAX_CONCURRENCY` (default `1`)
  - `APPS_BAR_RECOVERY_STALL_BURST_COUNT` (default `2`)

---

## 9. Limitations (current)

- Watcher lifecycle is single-fire.
- Breakout trigger logic is still fixed (close >= level).
- Fill-time TP reprice currently targets auto ladder flow (2/3 TP levels).
- Reprice applies sequential TP replaces; if interrupted, broker may hold partially updated levels (CLI marks this as skipped/partial and does not emit success TP-update event).
- Replace support remains limited to orders tracked in current session.

---

## 10. Future enhancements (optional)

- Explicit structured events for TP reprice skip/failure reasons.
- Broker-state reconciliation after partial replace scenarios.
- Reprice support for broader TP modes and optional stop-update re-optimization.
- Strategy schedules/re-arm and richer risk/session constraints.
- Deeper analytics modules (macro/flow/micro) feeding strategy presets.

---

## 11. Gotchas + debugging notes

- IBKR bar timestamps are bar-start times; strategy sees bars on close.
- Starting mid-bar means first seen bar may already be the close that triggers entry.
- For TP reprice debugging, check:
  - console lines beginning with `TP reprice ...`
  - `BreakoutTakeProfitsUpdated` in `apps/journal/events.jsonl`
  - child order events (`BracketChildOrderStatusChanged`, `BracketChildOrderFilled`).
- `positions` TP display reflects latest tracked TP updates after the TP update event is emitted.

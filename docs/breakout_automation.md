# Breakout Automation - Production Architecture (apps)

This document describes the production breakout automation architecture in `apps/`, including the current bracket and detached ladder execution behavior.

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
  - bracket mode (single TP/SL)
  - detached70 mode (2 TP + 2 SL with two OCA pairs)
  - detached mode (3 TP + 3 SL with three OCA pairs)
  - auto TP level calculation (`tp=auto tp_count=1|2|3` or shorthand `tp-1` / `tp-2` / `tp-3`).
- Auto TP mode uses historical bars to produce initial TP levels at watcher start.
- Emits strategy/order events for audit and CLI visibility.
- Breakout watcher remains single-fire (one trade attempt).

---

## 2. User workflow (CLI)

Operator runs apps CLI and starts a watcher.

Examples:
- Basic breakout:
  - `breakout AAPL level=190 qty=1`
- Single-TP bracket:
  - `breakout AAPL level=190 qty=1 tp=195 sl=187`
- Detached 2-TP ladder (auto-routes to `tp_exec=detached70`, fixed 70/30):
  - `breakout AAPL level=190 qty=100 tp=195-198 sl=187`
- Detached 3-TP ladder (auto-routes to `tp_exec=detached`):
  - `breakout AAPL level=190 qty=100 tp=195-198-205 sl=187`
- Detached 3-TP ladder with custom split:
  - `breakout AAPL level=190 qty=100 tp=195-198-205 sl=187 tp_alloc=50-30-20`
- Auto TP ladder (key/value):
  - `breakout XRTX level=0.42 qty=1000 tp=auto tp_count=3 sl=0.37`
- Auto ladder shorthand:
  - `breakout XRTX 0.42 1000 tp-2 sl=0.37`

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
  - Enforces mode matrix:
    - single TP => bracket
    - 2 TP ladder => detached70
    - 3 TP ladder => detached
- `apps/cli/position_origin_tracker.py`
  - Tracks position origin tag and latest TP/SL display state (including TP updates).
- `apps/cli/event_printer.py`
  - Prints strategy/order events with breakout leg lifecycle labeling (`entry`, `tp1`, `sl1`, `tp2`, `sl2`, `tp3`, `sl3`).

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
  - Supports explicit `anchor_price` (used by optional fill-based TP recalculation paths).

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
   - REPL sets TP qty split:
     - `tp-2` routes to detached70 and is normalized to 70/30.
     - `tp-3` routes to detached and defaults to 60/30/10 (override with `tp_alloc`).
4. Runner subscribes to slow bars (and optional fast bars) and streaming quotes.
5. On trigger, runner submits entry + exits:
   - bracket for single TP + SL.
   - detached70 for 2 TP + 2 SL (two OCA pairs).
   - detached for 3 TP + 3 SL (three OCA pairs).
6. Detached execution paths arm exits only after inventory confirmation (fill + execution qty + position check).
   - If confirmation fails, detached exits are not armed and the attempt fails safe.
7. Runner stops (single-fire) after one trade attempt.

---

## 5. Order and TP behavior

- Default entry type: limit at ask.
- Market entry is supported via explicit `entry=market`.

Bracket (single TP):
- `tp` + `sl` with single TP price.
- Stop-loss uses stop-limit: initial limit equals `sl`; when the stop is elected, limit reprices to touch (`bid` for SELL, `ask` for BUY).

Detached70 (2 TP + 2 SL):
- `tp=price1-price2` + `sl` (or `tp=auto tp_count=2`).
- Uses two independent OCA pairs: `tp1<->sl1`, `tp2<->sl2`.
- Quantity split is fixed to 70/30.
- After TP1 fully fills, SL2 is repriced to first stop-update level.

Detached (3 TP + 3 SL):
- `tp=price1-price2-price3` + `sl` (or `tp=auto tp_count=3`).
- Uses three independent OCA pairs: `tp1<->sl1`, `tp2<->sl2`, `tp3<->sl3`.
- Supports custom `tp_alloc` splits (must sum to qty).
- Stop repricing milestones:
  - after TP1 full fill: SL2 and SL3 are repriced to `stop_updates[0]`
  - after TP1+TP2 full fill: SL3 is repriced to `stop_updates[1]`

Mode routing and validation:
- `tp_exec=attached` does not support ladders; use single `tp=` + `sl=` bracket.
- If `tp_exec` is omitted, CLI auto-selects:
  - 2 TP ladder => `detached70`
  - 3 TP ladder => `detached`
- Detached modes (`detached70`, `detached`) disable fill-time TP reprice-on-fill.

---

## 6. Observability and audit

Emitted events (relevant groups):
- Breakout lifecycle: `BreakoutStarted`, `BreakoutBreakDetected`, `BreakoutFastTriggered`, `BreakoutConfirmed`, `BreakoutRejected`, `BreakoutStopped`.
- TP update: `BreakoutTakeProfitsUpdated`.
- Orders: `OrderIntent`, `OrderSent`, `OrderIdAssigned`, `OrderStatusChanged`, `OrderFilled`.
- Bracket/ladder children: `BracketChildOrderStatusChanged`, `BracketChildOrderFilled`.
- Ladder stop safety: `LadderStopLossReplaced`, `LadderStopLossReplaceFailed`, `LadderStopLossCancelled`, `LadderProtectionStateChanged`.
- Reconnect orphan-exit reconciliation: `OrphanExitOrderDetected`, `OrphanExitOrderCancelled`, `OrphanExitOrderCancelFailed`, `OrphanExitReconciliationCompleted`.
- Bar stream health/recovery: `BarStreamStalled`, `BarStreamRecovered`, `BarStreamRecoveryStarted`, `BarStreamRecoveryFailed`, `BarStreamCompetingSessionBlocked`, `BarStreamCompetingSessionCleared`, `BarStreamRecoveryScanScheduled`.

Display behavior:
- `event_printer` shows narrative lifecycle lines for entries/exits and protection state changes.
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
- Detached exits are armed only after inventory confirmation.
- Child-order incident handling (codes like `201/202/404`) attempts to keep remaining inventory protected (including emergency stop path).
- In paper mode, unrecoverable uncovered inventory path can flatten as a hard safety fallback.

---

## 8. Configuration inputs

Core CLI fields:
- Required: `symbol`, `level`, `qty`
- Optional common: `tp`, `sl`, `tp_exec`, `rth`, `bar`, `fast`, `fast_bar`, `max_bars`, `tif`, `outside_rth`, `entry`, `quote_age`, `account`, `client_tag`

Auto TP / reprice fields:
- `tp=auto`
- `tp_count=1|2|3`
- shorthand token: `tp-1|tp-2|tp-3`
- `tp_alloc=...` (primarily for 3-TP detached path; example: `60-30-10`)
- `tp_bar` / `tp_bar_size` (historical bar size for TP calculation)
- `tp_rth` / `tp_use_rth`
- `tp_timeout` (seconds for child TP ID readiness; relevant to optional TP reprice coordinator paths)

Defaults:
- If `rth=false`, breakout orders default `outside_rth=true`.
- Auto TP default allocation by active execution mode:
  - `tp-2 => detached70 => 70/30`
  - `tp-3 => detached => 60/30/10`

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
- Orphan-exit reconciliation on connect:
  - `APPS_ORPHAN_EXIT_SCOPE` (`client` or `all_clients`, default `all_clients`)
  - `APPS_ORPHAN_EXIT_ACTION` (`warn` or `cancel`, default `warn`)
    - `warn` is monitor-only (safe default).
    - `cancel` is explicit opt-in and attempts broker-side cancels for detected orphan exits.

---

## 9. Limitations (current)

- Watcher lifecycle is single-fire.
- Breakout trigger logic is still fixed (close >= level).
- Fill-time TP reprice-on-fill is disabled for detached ladder modes.
- Replace support remains limited to orders tracked in current session.

---

## 10. Future enhancements (optional)

- Explicit structured events for optional TP reprice coordinator paths.
- Broker-state reconciliation after partial replace scenarios.
- Reprice support for broader TP modes and optional stop-update re-optimization.
- Strategy schedules/re-arm and richer risk/session constraints.
- Deeper analytics modules (macro/flow/micro) feeding strategy presets.

---

## 11. Gotchas + debugging notes

- IBKR bar timestamps are bar-start times; strategy sees bars on close.
- Starting mid-bar means first seen bar may already be the close that triggers entry.
- For detached ladder debugging, check:
  - child order events (`BracketChildOrderStatusChanged`, `BracketChildOrderFilled`)
  - protection/stop events (`LadderProtectionStateChanged`, `LadderStopLossReplaced`, `LadderStopLossReplaceFailed`)
  - gateway incident correlation in CLI output (rejections and cancel races tied to leg/stage)
- `positions` TP display reflects latest tracked TP levels from breakout config and any emitted TP update events.

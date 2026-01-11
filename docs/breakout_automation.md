# Breakout Automation - Production Architecture (apps)

This document describes the production-ready architecture for the breakout automation as implemented in apps. It focuses on system responsibilities, data flow, and operational behavior.

---

## 1. What the system does

- Accepts a breakout configuration from the CLI (symbol, level, qty, optional TP/SL).
- Streams 1-minute market data from IBKR.
- Applies a simple breakout rule (break candle then confirm candle).
- On confirmation, submits either:
  - a market entry order, or
  - a bracket order with TP/SL if provided.
- Emits events for visibility and audit.
- Stops after one trade attempt (single-fire).

---

## 2. User workflow (CLI)

Operator runs the apps CLI and starts a breakout watcher:

- Example (market entry):
  - `breakout AAPL level=190 qty=1`
- Example (market entry + TP/SL bracket):
  - `breakout AAPL level=190 qty=1 tp=195 sl=187`

Stop or check status:
- `breakout status`
- `breakout stop AAPL`

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
  - Defines the `BarStreamPort` interface.
- `apps/adapters/market_data/ibkr_bars.py`
  - IBKR implementation of the bar stream (1-minute bars, live updates).

### Orders (reusable)
- `apps/core/orders/service.py`
  - Validates and submits orders; emits order lifecycle events.
- `apps/core/orders/models.py`
  - `OrderSpec` for standard orders and `BracketOrderSpec` for TP/SL.
- `apps/adapters/broker/ibkr_order_port.py`
  - Converts order specs to IBKR orders (including bracket/OCA logic).

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
3. Each bar is fed into the breakout logic.
4. On confirm candle:
   - If TP/SL provided: submit bracket order (parent + TP + SL, OCA).
   - Otherwise: submit a single market entry.
5. Order lifecycle events are emitted and logged.
6. Runner stops (single-fire).

---

## 5. Order behavior

- Market entry is default.
- If `tp` and `sl` are provided, a bracket order is placed:
  - Parent: market BUY.
  - Children: TP limit SELL and SL stop SELL.
  - Children are linked with an OCA group so one cancels the other.
- IBKR enforces TP/SL even if the app disconnects after submission.

---

## 6. Observability and audit

Events emitted:
- Breakout lifecycle: Started, BreakDetected, Confirmed, Rejected, Stopped.
- Order lifecycle: Intent, Sent, OrderIdAssigned, StatusChanged.

Logging:
- Console output via `event_printer`.
- JSONL audit log via `jsonl_logger` (path controlled by `APPS_EVENT_LOG_PATH`).

---

## 7. Operational behavior and guardrails

- Single-fire: each watcher stops after one trade attempt.
- Requires active IBKR connection; otherwise the CLI refuses to start a watcher.
- If the bar stream is canceled or the watcher is stopped, the stream is cleaned up.
- Paper-only guard is enforced by connection config (paper vs live ports).

---

## 8. Configuration inputs

CLI fields:
- Required: `symbol`, `level`, `qty`
- Optional: `tp`, `sl`, `rth`, `bar`, `max_bars`, `tif`, `outside_rth`, `account`, `client_tag`

Environment (IBKR):
- `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `PAPER_ONLY`, and related paper/live port settings.

---

## 9. Limitations (current)

- No auto re-arm; each watcher is single-fire.
- Breakout rule is fixed (break candle then confirm candle).
- No volatility-based sizing or dynamic TP/SL yet.

---

## 10. Future enhancements (optional)

- Automated TP/SL from volatility or historical structure.
- Sizing off for profits or stop losses
- Entry types beyond market (limit/stop entry).
- Strategy re-arming and session scheduling.
- Real-time PnL tracking and fill-based performance stats.

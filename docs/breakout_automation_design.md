# Breakout Automation – Design Sketch

This document describes a simple, candle‑based breakout automation built on top of the existing IB/CLI framework in this repo. It is **design only** – a blueprint for how we will wire it up.

The core idea: you configure a breakout setup once (symbol, level, stop, etc.), start a watcher, and it will listen to 1‑minute candles. When a specific pattern appears, it automatically sends a bracket order via Interactive Brokers and then stops.

---

## 1. User flow – how you use it

1. **Start the CLI**
   - Run something like: `python -m apps.api.cli`.
   - The CLI shows a small menu, for example:
     - `1` – Manual paper trading (existing `b` / `k` / `q` loop).
     - `2` – Breakout strategy (automated).

2. **Choose “Breakout strategy”**
   - Either pass parameters as flags (non‑interactive):  
     `python -m apps.api.cli breakout --symbol AAPL --level 190 --stop 187 --qty 50 --tp 195 --rth-only`
   - Or let the CLI prompt you interactively:
     - `Symbol [AAPL]:`
     - `Breakout level L [190.00]:`
     - `Stop loss [187.00]:`
     - `Take‑profit (optional, default from %):`
     - `Shares [default qty]:`
     - `RTH only? [y/N]:`
   - These inputs are collected into a small `BreakoutConfig` object.

3. **CLI starts the breakout watcher**
   - The CLI:
     - creates **one** `IBClient` and **one** `BrokerService`,
     - connects to IB (paper or live, guarded by `PAPER_ONLY`),
     - passes `client`, `service`, and the `BreakoutConfig` into a breakout runner function.

4. **Watcher listens for the breakout pattern**
   - The breakout runner subscribes to 1‑minute bars for the chosen symbol and applies the rule:
     - **Break candle**: first bar where `high >= L`.
     - **Confirm candle**: next bar where `open >= L`.
   - It maintains a small state machine per symbol:
     - `IDLE` → waiting for first break.
     - `AWAIT_CONFIRM` → waiting for confirm candle.
     - `DONE` → after a trade is sent (single‑fire).

5. **On confirmation, the order is sent**
   - When the confirm candle appears:
     - The breakout strategy decides the exact entry price (e.g. limit at `max(L, confirm_open)` or at L with a small offset).
     - It calls a method on `BrokerService` to place a **bracket**:
       - Parent = BUY (market / limit / stop, depending on config).
       - Children = TP and SL (either broker‑native stops, or later “soft”/conditional logic).

6. **Bracket rests at IB, watcher exits**
   - IB now holds the bracket order and enforces TP/SL even if your app disconnects.
   - The watcher logs the result, updates the journal, and returns control back to the CLI (or simply exits).
   - This is a **single‑fire** behavior: it runs for one configured setup and then stops.

---

## 2. Files and responsibilities

At a high level, we keep the same layered structure:

- **CLI layer** – user interaction and wiring.
- **Strategy layer** – breakout rule + state machine.
- **Service layer** – high‑level trading operations.
- **IB adapter / order builder** – low‑level IB calls and order objects.

### 2.1 CLI entry – `apps/api/cli.py`

- Loads environment variables (defaults for symbol, qty, TP/SL, IB host/port, etc.).
- Enforces paper trading guard (`PAPER_ONLY` vs port).
- Creates a single instance of:
  - `IBClient` (connection to IB).
  - `BrokerService` (high‑level trading operations).
- Presents a **mode selector**:
  - Manual paper trading REPL (existing behavior).
  - Breakout automation.
- For breakout mode:
  - Collects user parameters (flags or prompts).
  - Builds a `BreakoutConfig` object.
  - Calls something like:
    - `run_breakout_watcher(config, client, service)`.

CLI does **not** implement the breakout logic itself – it just wires the pieces together and handles user I/O.

### 2.2 IB adapter – `apps/ib/client.py`

- Wraps `ib_insync.IB` for:
  - Connecting / disconnecting.
  - Qualifying stock contracts.
  - Requesting market data / reference prices.
  - Placing orders and waiting for IDs / statuses.
  - Canceling all open trades.
- For breakout automation specifically:
  - Provides the connection used by the breakout runner to request 1‑minute bars:
    - e.g. `reqHistoricalData(contract, barSizeSetting="1 min", keepUpToDate=True, whatToShow="TRADES", useRTH=...)`.
  - The same `IBClient` instance is shared between:
    - the breakout watcher, and
    - the `BrokerService` for order placement.

### 2.3 Broker service – `apps/broker/service.py`

- High‑level orchestration for trading, independent of specific strategies.
- Existing responsibilities:
  - Given symbol, qty, TP/SL percentages, it:
    - qualifies the contract via `IBClient`,
    - gets a reference price,
    - builds a bracket via `apps/ib/orders.py`,
    - sends the parent, waits for an order ID, then sends the children,
    - writes INTENT/ACK/CANCEL events to the journal.
- For breakout automation we add a method such as:
  - `place_buy_breakout(symbol, qty, entry_price, tp_price, sl_price)`  
    (exact signature to be finalised).
- That method:
  - Validates prices (e.g. `sl_price < entry_price` for longs).
  - Uses `IBClient` to qualify the contract.
  - Uses order helpers in `apps/ib/orders.py` to build a bracket for this specific entry type (market/limit/stop).
  - Places parent + children, journals INTENT and ACK, logs a concise summary.

Important: **strategy logic does not live here** – this layer is only concerned with “execute this concrete trade safely and consistently”.

### 2.4 Order builders – `apps/ib/orders.py`

- Contains small helpers that build IB order objects, hiding the low‑level details:
  - `build_buy_bracket(qty, entry_price, tp_price, sl_price, tif="DAY")`.
  - (Possibly) `build_buy_breakout_bracket(...)` if we need a distinct variant.
- They decide:
  - Whether the parent is a market, limit, or stop order.
  - How the OCA group is set up for TP/SL.
  - Which child order “transmits” the whole bracket.
- Breakout strategy calls into `BrokerService`, and the service uses these helpers to actually build the orders.

### 2.5 Breakout strategy & runner – new modules

These are **new** pieces we will add, living in a new `apps/strategies/` package (or similar). A simple first pass:

- `apps/strategies/breakout_config.py` (optional)
  - Defines a small config object, e.g.:
    - `symbol`, `level` (L), `stop_loss`, `take_profit`, `qty`, `rth_only`, `outside_rth`, `dry_run`, etc.

- `apps/strategies/breakout_strategy.py`
  - Implements the **pure breakout rule**:
    - Maintains state (`IDLE`, `AWAIT_CONFIRM`, `DONE`).
    - Exposes something like `on_bar(bar, state, config)` → returns:
      - updated `state`,
      - optional “action” describing a trade to place (entry price, TP, SL, qty).
  - This module does not talk to IB directly – it just decides *when* to trade and *with what parameters*.

- `apps/strategies/breakout_watcher.py`
  - Holds the **runner/loop** that ties everything together.
  - Responsibilities:
    - Accept `BreakoutConfig`, `IBClient`, and `BrokerService`.
    - Qualify the contract (once).
    - Subscribe to 1‑minute bars for that contract.
    - For each new bar:
      - Pass it into `breakout_strategy.on_bar(...)`.
      - If the strategy returns an action, call `BrokerService` to place the bracket.
      - After sending, move to `DONE` and unsubscribe / stop.
    - Handle connection drops and logging (e.g. log and stop, don’t retroactively trade on missed bars).

By separating strategy logic (`breakout_strategy.py`) from the runner (`breakout_watcher.py`), we can:

- unit‑test the state machine with synthetic candles;
- keep IB‑specific details in one place (the watcher and the service);
- add more strategies later without changing the CLI or service layers much.

---

## 3. Behavior choices (current thinking)

These are the initial design choices for the first version:

- **Breakout rule**:
  - First 1‑minute candle where `high >= L` is the “break” candle.
  - Next 1‑minute candle where `open >= L` is the “confirm” candle.
  - On the confirm candle, we decide to enter long.

- **Entry type**:
  - Likely a **limit order** near the confirm open / above L:
    - e.g. `entry = max(L, confirm_open)` or `entry = L + small_offset`.
  - This can be tuned and parameterised later.

- **Stops / take‑profits**:
  - First version uses **broker‑native bracket** (hard TP/SL at IB) for safety when running unattended.
  - Later versions can add “soft”/conditional stops managed by the watcher, with a hard disaster stop still at IB.

- **Session rules**:
  - Configurable `rth_only` vs `eth_ok`:
    - `rth_only=True` → filter bars to regular trading hours; ignore pre/post.
    - `eth_ok=True` → allow the rule to fire in extended hours; orders can use `outsideRth=True`.

- **Single‑fire vs re‑arm**:
  - The breakout watcher is **single‑use** per configuration in v1:
    - Runs until it either:
      - triggers and sends a trade, or
      - is cancelled / session ends, etc.
    - Then stops.
  - A later version could add automatic re‑arming (e.g. after exit) as a separate mode.

- **Connection loss**:
  - Before entry is placed:
    - If the connection drops, log and stop; do not retroactively place a trade based on bars you might have missed.
  - After a bracket is placed:
    - IB holds TP/SL, so risk is still controlled.
    - On reconnect, you can query open orders/positions to rebuild state (future enhancement).

---

## 4. Summary of “who talks to whom”

- User → `apps/api/cli.py`
  - Chooses mode and provides breakout parameters.

- `apps/api/cli.py` → `IBClient` + `BrokerService`
  - Creates them once and shares them for the whole session.

- `apps/api/cli.py` → `apps/strategies/breakout_watcher.py`
  - Calls a function like `run_breakout_watcher(config, client, service)`.

- `breakout_watcher` → `IBClient`
  - Subscribes to 1‑minute bars.
  - Uses the existing IB connection.

- `breakout_watcher` → `breakout_strategy`
  - Feeds each bar into the strategy state machine.
  - Receives “place trade” actions from the strategy.

- `breakout_watcher` → `BrokerService`
  - When the strategy says “go”, calls a method like `place_buy_breakout(...)`.

- `BrokerService` → `apps/ib/orders.py`
  - Uses order builders to create parent / TP / SL IB orders.

- `BrokerService` → `IBClient`
  - Sends orders, waits for IDs/status changes.

- `BrokerService` → `apps/ib/journal.py`
  - Writes INTENT / ACK / CANCEL events to a JSONL journal.

This keeps the breakout automation understandable, testable, and extendable: the CLI handles UX, the watcher runs the continuous loop, the strategy encodes your candle rule, the service executes trades, and the IB adapter + order builders handle the low‑level API details.


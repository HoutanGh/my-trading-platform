# Breakout Automation – MVP

This document describes a **minimum viable** candle‑based breakout automation built on top of the existing IB/CLI framework in this repo.

The core idea: you configure a breakout setup once (symbol + breakout level L), start a watcher, and it will listen to 1‑minute candles. When a simple pattern appears, it sends a **single market buy** via Interactive Brokers and then stops.

---

## 1. User flow – how you use it

1. **Start the CLI**
   - Run something like: `python -m apps.api.cli`.
   - The CLI shows a small menu, for example:
     - `1` – Manual paper trading (existing `b` / `k` / `q` loop).
     - `2` – Breakout strategy (automated).

2. **Choose “Breakout strategy”**
   - For the MVP, use simple **interactive prompts**:
     - `Symbol [AAPL]:`
     - `Breakout level L [190.00]:`
   - Quantity comes from the existing `DEFAULT_QTY` env var.  
     (No TP/SL configuration in the MVP.)

3. **CLI starts the breakout watcher**
   - The CLI:
     - creates **one** `IBClient` and **one** `BrokerService`,
     - connects to IB (paper or live, guarded by `PAPER_ONLY`),
     - passes `client`, `service`, and the user inputs into a breakout runner function.

4. **Watcher listens for the breakout pattern**
   - The breakout runner subscribes to 1‑minute bars for the chosen symbol and applies the rule:
     - **Break candle**: first bar where `high >= L`.
     - **Confirm candle**: next bar where `open >= L`.
   - Internally it keeps a simple flag:
     - Before any break: `break_seen = False`.
     - When the first bar with `high >= L` appears: set `break_seen = True` and remember that bar.
     - On the very next bar:
       - if `open >= L` → this is the confirm candle (go),
       - else → the setup failed (do nothing).

5. **On confirmation, the order is sent**
   - When the confirm candle appears:
     - The breakout logic sends a **market BUY** using `DEFAULT_QTY`.
     - (If the market is thin and you later prefer limit at/near the ask, that can be added as an option, but is not part of the MVP.)

6. **Watcher exits**
   - After sending the market order, the watcher:
     - logs what it did,
     - writes an event to the journal,
     - stops listening and returns control back to the CLI.
   - This is **single‑fire**: one configured breakout → one attempt → stop.

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
  - Breakout automation (MVP).
- For breakout mode (MVP):
  - Prompts for `symbol` and `level L`.
  - Uses `DEFAULT_QTY` for quantity.
  - Calls something like:
    - `run_breakout_watcher(symbol, level, client, service)`

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
- For the breakout MVP we add a **simple entry method**, for example:
  - `place_breakout_market_buy(symbol: str, qty: int)`  
    (exact name/signature can be chosen when we implement it.)
- That method:
  - Qualifies the contract via `IBClient`.
  - Builds a **market BUY** order (no TP/SL in MVP).
  - Places the order, waits for an acknowledgement.
  - Writes INTENT and ACK events to the journal.

Important: **strategy logic does not live here** – this layer is only concerned with “execute this concrete trade safely and consistently”.

### 2.4 Order builders – `apps/ib/orders.py`

- Contains small helpers that build IB order objects, hiding the low‑level details:
  - `build_buy_bracket(qty, entry_price, tp_price, sl_price, tif="DAY")`
  - (Possibly) `build_buy_breakout_bracket(...)` if we need a distinct variant.
- They decide:
  - Whether the parent is a market, limit, or stop order.
  - How the OCA group is set up for TP/SL.
  - Which child order “transmits” the whole bracket.
- Breakout strategy calls into `BrokerService`, and the service uses these helpers to actually build the orders.

### 2.5 Breakout strategy & runner – new modules

These are **new** pieces we will add, living in a new `apps/strategies/` package (or similar). For the MVP we keep them very small:

- `apps/strategies/breakout_watcher.py`
  - Holds the loop that:
    - Accepts `symbol`, `level L`, `IBClient`, and `BrokerService`.
    - Qualifies the contract once.
    - Subscribes to 1‑minute bars for that contract.
    - Implements the simple `break_seen` logic described above.
    - On confirmation, calls `BrokerService.place_breakout_market_buy(...)`.
    - Logs what happened and then stops.

Later we can extract a separate `breakout_strategy.py` if/when the logic becomes more complex (TP/SL, re‑arming, risk sizing, etc.).

---

## 3. Behavior choices (MVP)

These are the behavior choices for the **MVP only**:

- **Breakout rule**:
  - First 1‑minute candle where `high >= L` is the “break” candle.
  - Next 1‑minute candle where `open >= L` is the “confirm” candle.
  - If the confirm candle appears, we buy; otherwise we do nothing and stop.

- **Entry type**:
  - Single **market BUY** using `DEFAULT_QTY`.
  - No TP/SL or bracket orders in the MVP.

- **Session rules**:
  - MVP assumes “whatever bars IB returns for 1‑minute TRADES”.
  - RTH vs ETH filters can be added later as flags/options.

- **Single‑fire**:
  - The breakout watcher is **single‑use** per configuration:
    - Runs until it either:
      - triggers and sends the market order, or
      - determines the setup failed (no confirm candle).
    - Then stops. No automatic re‑arming in the MVP.

- **Connection loss**:
  - If the connection drops before entry is placed:
    - Log and stop; do not attempt to reconstruct or chase missed breakouts.
  - After a market order is placed:
    - Normal IB position handling applies; the watcher itself is done.

---

## 4. Summary of “who talks to whom”

- User → `apps/api/cli.py`
  - Chooses mode and provides breakout parameters (`symbol`, `level`).

- `apps/api/cli.py` → `IBClient` + `BrokerService`
  - Creates them once and shares them for the whole session.

- `apps/api/cli.py` → `apps/strategies/breakout_watcher.py`
  - Calls a function like `run_breakout_watcher(symbol, level, client, service)`.

- `breakout_watcher` → `IBClient`
  - Subscribes to 1‑minute bars.
  - Uses the existing IB connection.

- `breakout_watcher` → `BrokerService`
  - When the confirm candle appears, calls a method like `place_breakout_market_buy(symbol, qty)`.

- `BrokerService` → `apps/ib/orders.py`
  - (For MVP) may use a simple helper to build a market BUY order.

- `BrokerService` → `IBClient`
  - Sends orders, waits for IDs/status changes.

- `BrokerService` → `apps/ib/journal.py`
  - Writes INTENT / ACK / CANCEL events to a JSONL journal.

This keeps the breakout automation understandable and small: the CLI handles UX, the watcher runs the continuous loop, the simple breakout rule decides when to buy, the service executes the trade, and the IB adapter + order builders handle the low‑level API details.


I need to time how fast the order is placed.
# Trading Platform Architecture

This repository is a small trading playground built around Interactive Brokers, with a clear split between:

- a simple command line interface for user interaction
- a broker service layer for business rules and journalling
- an IB adapter layer that hides `ib_insync` details
- supporting notebooks and docs for experiments and design

The aim is to keep the trading logic understandable, testable, and easy to extend.

## High‑level flow

The typical paper‑trading loop works like this:

1. You run the CLI entry point.
2. The CLI initialises an `IBClient` and a `BrokerService`.
3. You press keys (for example `b` to place a buy bracket).
4. The `BrokerService` decides prices and quantities, builds a bracket order, and sends it via the IB client.
5. As things happen (intent, acknowledgement, cancels), events are written to a journal file.

In short:

`CLI → BrokerService → IB client + order builder → Interactive Brokers`

## Components and responsibilities

### CLI / API layer

- `apps/api/cli.py`
  - Loads environment variables such as default symbol, quantity, and TP/SL percentages.
  - Enforces a safety check so that paper mode only uses the paper‑trading port.
  - Creates an `IBClient` and a `BrokerService`.
  - Runs a small REPL:
    - `b` – place a buy bracket using the current defaults.
    - `k` – cancel all open orders.
    - `q` – quit and disconnect.

This is the only place that talks directly to the user.

### Broker service layer

- `apps/broker/service.py`
  - High‑level orchestration for trading actions.
  - Given a symbol, quantity, and TP/SL percentages, it:
    - asks the `IBClient` to qualify the contract
    - asks the client for a reference price
    - calculates entry, take‑profit, and stop‑loss prices
    - calls the order builder to create a bracket
    - sends the parent order, waits for an order ID, then sends the children
  - Writes structured events to the journal (INTENT, ACK, CANCEL_ALL).
  - Logs a concise summary of what it just did.

The `BrokerService` knows about trading rules, but not about `ib_insync` internals.

### IB adapter and order‑building layer

- `apps/ib/client.py`
  - Thin wrapper around `ib_insync.IB`.
  - Responsibilities:
    - connect/disconnect to IB
    - qualify stock contracts
    - request short‑lived market data to get a reference price
    - place orders and return `Trade` objects
    - wait for an order ID or status with simple polling
    - cancel all open trades

- `apps/ib/orders.py`
  - Contains helpers for building IB order objects.
  - Currently provides `build_buy_bracket(qty, entry_price, tp_price, sl_price, tif="DAY")`.
  - Hides IB‑specific details such as:
    - whether the parent is a market or limit order
    - setting an OCA (one‑cancels‑all) group for the children
    - transmit flags so that the whole bracket is submitted correctly

By keeping this logic here, higher layers do not need to worry about how bracket orders are wired up on the IB side.

### Journalling

- `apps/ib/journal.py`
  - Manages a `journal/` directory under `apps/ib/`.
  - Appends JSON Lines (`.jsonl`) events to `events.jsonl`.
  - Every event is timestamped in UTC ISO 8601 format.

The journal gives you a lightweight audit trail of intents, acknowledgements, and cancellations.

### Notebooks and docs

- `notebooks/00_sandbox.ipynb`
  - A playground for experimenting with ideas, APIs, and strategies without touching the main app.

- `docs/my-trading-platform.drawio`
  - A visual diagram of the architecture and data flow.

These are optional helpers to think about the system, not part of the runtime path.

## Extending the platform

Some natural next steps:

- **More order types**  
  Add new helpers in `apps/ib/orders.py` for short brackets, trailing stops, or more advanced structures. The `BrokerService` can then call them without learning IB details.

- **More strategies**  
  Add new methods to `BrokerService` (or separate strategy modules) that compute quantities and price levels in different ways, while still using the same client and journalling.

- **Alternative front‑ends**  
  Keep the current CLI as a thin layer, but later add a FastAPI app or web UI that calls into the same `BrokerService` API.

Throughout, the goal is to keep responsibilities narrow: user interfaces at the top, trading rules in the middle, IB details at the bottom, with journalling running alongside for observability.

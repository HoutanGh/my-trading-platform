# Trading Platform & Daily P&L Calendar

This repository is a small trading playground built around Interactive Brokers, plus a minimal **daily P&L calendar** backed by Postgres and a FastAPI/React API + web UI.

The project is split between:

- a simple command line interface for interactive paper trading
- a broker service layer for business rules and journalling
- an IB adapter layer that hides `ib_insync` details
- a Postgres‑backed `daily_pnl` table, fed from Flex CSV and the IB API
- a FastAPI endpoint that serves daily P&L data
- a React calendar view that visualises daily realized P&L
- supporting notebooks and docs for experiments and design

The aim is to keep both the trading logic and the P&L reporting flow understandable, testable, and easy to extend.

## High‑level flows

### 1. Paper‑trading loop

The typical paper‑trading loop works like this:

1. You run the CLI entry point.
2. The CLI initialises an `IBClient` and a `BrokerService`.
3. You press keys (for example `b` to place a buy bracket).
4. The `BrokerService` decides prices and quantities, builds a bracket order, and sends it via the IB client.
5. As things happen (intent, acknowledgement, cancels), events are written to a journal file.

In short:

`CLI → BrokerService → IB client + order builder → Interactive Brokers`

### 2. Daily P&L calendar flow

The daily P&L calendar works in three stages:

1. **Store daily realized P&L in Postgres**
   - Backfill history from IB Flex CSV using `apps/data/ingest_flex.py`.
   - Optionally update today’s P&L from the IB API using `apps/data/ingest_ib.py`.
   - All data lands in a `daily_pnl` table.
2. **Expose P&L over HTTP**
   - A FastAPI app (`apps/api/main.py`) serves a `GET /pnl/daily` endpoint that reads from `daily_pnl`.
3. **Render a calendar in the browser**
   - A small React app in `web/` calls the API and shows a colour‑coded calendar by day, with monthly and weekly totals.

## Components and responsibilities

### Trading CLI layer

- `apps/api/cli.py`
  - Loads environment variables such as default symbol, quantity, and TP/SL percentages.
  - Enforces a safety check so that paper mode only uses the paper‑trading port.
  - Creates an `IBClient` and a `BrokerService`.
  - Runs a small REPL:
    - `b` – place a buy bracket using the current defaults.
    - `k` – cancel all open orders.
    - `q` – quit and disconnect.

This is the only place that talks directly to the user for trading.

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

### Daily P&L storage (Postgres)

- `apps/data/db.py`
  - Reads `DATABASE_URL` from the environment.
  - Provides a `get_conn()` context manager for sharing Postgres connections.
  - Exposes `ensure_schema()` which creates the `daily_pnl` table if it does not exist.

- `apps/data/upload.py`
  - Defines `upsert_daily_pnl(account, trade_date, realized_pnl, source)` which inserts or updates a single row in `daily_pnl`.

- `apps/data/ingest_flex.py`
  - Reads an IB Flex CSV (e.g. `data/raw/Daily_PL.csv`).
  - Aggregates `FifoPnlRealized` by `TradeDate` and upserts totals into `daily_pnl` for a given account.
  - Intended usage:
    - `python -m apps.data.ingest_flex --csv data/raw/Daily_PL.csv --account DU123456`

- `apps/data/ingest_ib.py`
  - Connects to IB using the existing `IBClient`.
  - Logs current positions and subscribes to P&L for the configured account.
  - Prints a snapshot of daily / unrealized / realized P&L.
  - This is a first step towards piping today’s realized P&L into `daily_pnl` via the same upsert helper.

### FastAPI layer (P&L API)

- `apps/api/main.py`
  - Creates the FastAPI application.
  - Configures CORS using `CORS_ORIGINS` from `apps/api/settings.py` so the React dev server can talk to it.
  - On startup, calls `ensure_schema()` to guarantee `daily_pnl` exists.
  - Exposes a simple `/health` endpoint.
  - Registers P&L routes.

- `apps/api/settings.py`
  - Loads environment variables with `python-dotenv`.
  - `FRONTEND_ORIGINS` / `FRONTEND_ORIGIN` – comma‑separated list of allowed origins for CORS (defaults to `http://localhost:5173`).
  - `DEFAULT_ACCOUNT` – optional default account for local testing.

- `apps/api/deps.py`
  - Provides a FastAPI dependency `db_conn()` that yields a shared Postgres connection using `get_conn()`.

- `apps/api/routes/pnl.py`
  - Defines `GET /pnl/daily`:
    - Query parameters:
      - `account` (required) – IB account id.
      - `start_date`, `end_date` (optional, `YYYY-MM-DD`).
    - Reads from `daily_pnl` via `db_conn()`.
    - Returns a list of objects with `account`, `trade_date`, `realized_pnl`, and `source`.

### P&L calendar web UI

- `web/`
  - A small React + TypeScript + Vite app.
  - Calls the FastAPI backend at `GET /pnl/daily` and renders a calendar view.
  - Shows:
    - one card per day with realized P&L and optional trade count
    - monthly total P&L
    - weekly totals per row
    - basic colouring for positive / negative / flat days and weekends

Key pieces:

- `web/src/App.tsx`
  - Holds the calendar logic.
  - Reads:
    - `VITE_API_BASE_URL` – base URL for the API (defaults to `http://localhost:8000`).
    - `VITE_DEFAULT_ACCOUNT` – default account to query (defaults to `"paper"` if not set).
  - On month or account change, fetches `/pnl/daily` for the visible month.

- `web/App.css`
  - Styles the calendar grid, weekly totals, and positive/negative highlighting.

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

- `docs/daily_pnl_design.md`
  - Design notes for the Postgres `daily_pnl` table and P&L ingestion flows (Flex CSV, IB API).

These are optional helpers to think about the system, not part of the runtime path.

## Running the daily P&L calendar locally

### 1. Start Postgres and set `DATABASE_URL`

You need a Postgres instance and a database for analytics (for example `my_trading_db`).

Example using Docker:

- `docker run --name my-postgres -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16`

Then create a database and set:

- `export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/my_trading_db"`

The API will call `ensure_schema()` on startup to create the `daily_pnl` table if needed.

### 2. Backfill historical P&L from Flex

Export an IB Flex report with `TradeDate` and `FifoPnlRealized` columns to `data/raw/Daily_PL.csv` (or another path), then:

- `python -m apps.data.ingest_flex --csv data/raw/Daily_PL.csv --account DU123456`

This aggregates realized P&L per day and upserts into `daily_pnl`.

### 3. Optional: experiment with live P&L via IB API

Set your IB connection env vars, for example:

- `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `IB_ACCOUNT`

Then run:

- `python -m apps.data.ingest_ib`

This connects to IB using the existing client and logs a snapshot of positions and P&L. You can evolve this script to upsert today’s realized P&L into `daily_pnl` using `upsert_daily_pnl`.

### 4. Run the FastAPI backend

Install Python dependencies (ideally in a virtualenv):

- `pip install -r requirements.txt`

Then start the API (default port 8000):

- `uvicorn apps.api.main:app --reload`

If you run the frontend from a different origin, set:

- `export FRONTEND_ORIGINS="http://localhost:5173"`

### 5. Run the React P&L calendar

From the `web/` directory:

- `npm install`
- `npm run dev` (defaults to `http://localhost:5173`)

Optional frontend env vars (set in `web/.env` or your shell):

- `VITE_API_BASE_URL="http://localhost:8000"`
- `VITE_DEFAULT_ACCOUNT="DU123456"`

With the backend and frontend both running, the calendar will fetch daily P&L from `/pnl/daily` and display it by day, with monthly and weekly totals.

## Extending the platform

Some natural next steps:

- **More order types**  
  Add new helpers in `apps/ib/orders.py` for short brackets, trailing stops, or more advanced structures. The `BrokerService` can then call them without learning IB details.

- **More strategies**  
  Add new methods to `BrokerService` (or separate strategy modules) that compute quantities and price levels in different ways, while still using the same client and journalling.

- **Alternative front‑ends**  
  Keep the current CLI as a thin layer, but later add more FastAPI endpoints or richer web UIs that call into the same `BrokerService` and `daily_pnl` API.

- **P&L drill‑down**  
  Implement a `daily_symbol_pnl` table (see `docs/daily_pnl_design.md`) and extend the API/UI so clicking a day shows per‑symbol contributions.

Throughout, the goal is to keep responsibilities narrow: user interfaces at the top, trading rules in the middle, IB details at the bottom, with journalling and P&L storage running alongside for observability.

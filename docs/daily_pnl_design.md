# Daily P&L Storage and IB Integration (Design Notes)

These notes capture the current design so you can resume later without rereading the whole discussion.

## 1. Goals

- Store **daily realized P&L** in local Postgres as a durable-ish cache.
- Use **IB Flex CSV** to backfill history.
- Use **ib_insync** (via existing `IBClient`) to update **today’s** P&L.
- Keep the architecture clean and re-usable for a future FastAPI/React dashboard.
- Be able to **drop and rebuild** from Flex + IB history if needed.

## 2. Core Data Model

### Table: `daily_pnl`

- `id BIGSERIAL PRIMARY KEY`
- `account TEXT NOT NULL` – IB account id (e.g. `DU123456`).
- `trade_date DATE NOT NULL` – P&L date in IB’s sense of the day.
- `realized_pnl DOUBLE PRECISION NOT NULL` – realized P&L for that day.
- `source TEXT NOT NULL` – e.g. `'flex'`, `'ib_api'`.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `UNIQUE (account, trade_date, source)`

Notes:

- No `unrealized_pnl` column for now (can be added later).
- `source` lets you keep multiple views (Flex vs API) and reconcile later.

### Possible Future Table: `daily_symbol_pnl`

For drill-down when clicking a day:

- `id BIGSERIAL PRIMARY KEY`
- `account TEXT NOT NULL`
- `trade_date DATE NOT NULL`
- `symbol TEXT NOT NULL`
- `realized_pnl DOUBLE PRECISION NOT NULL`
- `source TEXT NOT NULL` (likely `'flex'`)
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `UNIQUE (account, trade_date, symbol, source)`

Use case: when you click a day in the UI, show per-symbol P&L for that day.

## 3. Postgres Mental Model and Setup

High-level idea:

- Postgres is a server process listening on a port (default `5432`).
- You connect to it using:
  - CLI (`psql`).
  - Python (via a driver like `psycopg`).
- For this project, it’s a local analytics cache, not production HA.

Minimal setup workflow:

1. **Run Postgres locally**
   - Example via Docker:
     - `docker run --name my-postgres -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16`

2. **Create a database**
   - `psql -h localhost -U postgres`
   - Inside `psql`:
     - `CREATE DATABASE my_trading_db;`
     - `\q` to quit.

3. **Define connection string for Python**
   - Environment variable:
     - `export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/my_trading_db"`

4. **Schema creation**
   - A small Python helper (e.g. `apps/data/db.py`) can:
     - Read `DATABASE_URL`.
     - Connect with `psycopg`.
     - Run a `CREATE TABLE IF NOT EXISTS daily_pnl (...)` statement.
   - This avoids manual migrations for now.

## 4. Flex Backfill Flow (Historical Realized P&L)

Input: Flex CSV similar to `data/raw/Daily_PL.csv` with columns:

- `TradeDate`
- `FifoPnlRealized`
- `Symbol` (optional but useful for per-symbol table)
- Possibly `AccountId` (if present; otherwise pass account externally).

Python script location (planned): `apps/data/ingest_flex.py`

Responsibilities:

- Read the CSV (via `pandas` or `csv`).
- Parse:
  - `TradeDate` → `date`.
  - `FifoPnlRealized` → `float`.
- Group by `TradeDate` (and optionally `AccountId` if present).
- Sum `FifoPnlRealized` to get `realized_pnl` per day.
- Insert or UPSERT into `daily_pnl` with:
  - `account` (from `AccountId` or CLI/env).
  - `trade_date` (from `TradeDate`).
  - `realized_pnl` (sum for that day).
  - `source = 'flex'`.

Optionally:

- Also group by `(TradeDate, Symbol)` and write to `daily_symbol_pnl` for drill-down.

Intended usage:

- Ensure `DATABASE_URL` and `IB_ACCOUNT` are set.
- Run:
  - `python -m apps.data.ingest_flex --csv data/raw/Daily_PL.csv --account DU123456`

You can re-run this any time after dropping tables to rebuild the cache.

## 5. Daily P&L via ib_insync (Today Only)

We reuse your existing IB adapter:

- `apps/ib/client.py` defines `IBClient` which wraps `ib_insync.IB`.
- Config (already used in `apps/api/cli.py`):
  - `IB_HOST`
  - `IB_PORT`
  - `IB_CLIENT_ID`
  - `IB_ACCOUNT`
  - Loaded via `.env` + `python-dotenv`.

Planned script: `apps/data/ingest_ib.py`

Flow:

1. Load env (`.env`).
2. Create `IBClient` and `connect(readonly=True)` using the same env values as the CLI.
3. Call `ib.reqPnL(account)`:
   - Sleep briefly to allow IB to push data.
   - Read `pnl.realizedPnL` (float; default to `0.0` if `None`).
4. Get today’s trading date:
   - `ib.reqCurrentTime().date()` → `trade_date`.
5. UPSERT into `daily_pnl` with:
   - `account`
   - `trade_date`
   - `realized_pnl` (from `pnl.realizedPnL`)
   - `source = 'ib_api'`.
6. Disconnect `IBClient`.

Intended usage:

- Ensure `DATABASE_URL`, `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `IB_ACCOUNT` are set.
- Run manually or via cron at end of day:
  - `python -m apps.data.ingest_ib`

This gives you an up-to-date row for “today” from the API, which can complement or cross-check Flex data later.

## 6. Timezone Handling

We keep it simple for now:

- `trade_date` is stored as a `DATE` aligned with IB’s notion of the **trading day**.
- Flex `TradeDate` already reflects IB’s trading date.
- The API version uses `ib.reqCurrentTime().date()` as `trade_date`.

In the dashboard:

- Show `trade_date` as a simple `YYYY-MM-DD` date (no timezone confusion).
- If you later store timestamps for detailed trades, store as `TIMESTAMPTZ` in UTC and convert to local time at the API/UI layer.

## 7. psycopg vs ORM (Choice)

Definitions:

- **psycopg-only**:
  - A lightweight driver to talk to Postgres.
  - You write SQL manually: `INSERT`, `SELECT`, etc.
  - Simple and explicit; good for a small analytics cache.

- **ORM** (e.g. SQLAlchemy/SQLModel):
  - You define Python classes (`DailyPnl`) and let the library generate SQL.
  - Helpful for big schemas and complex relationships, but more concepts to learn.

For this project:

- Start with **psycopg-only**:
  - Small number of tables.
  - Rebuildable cache.
  - Less complexity while you get comfortable with Postgres.
- If the schema grows significantly and you want deeper abstractions, we can later introduce an ORM without changing the underlying tables.

## 8. Click-to-Drill-Down Design (UI/Analytics)

Summary-level (per day):

- Query:
  - `SELECT trade_date, realized_pnl FROM daily_pnl WHERE account = ? ORDER BY trade_date;`
- Each row becomes a clickable item (e.g. in React).

Detail-level (per symbol on that day):

- With `daily_symbol_pnl`:
  - `SELECT symbol, realized_pnl FROM daily_symbol_pnl WHERE account = ? AND trade_date = ? ORDER BY realized_pnl DESC;`
- Future extensions:
  - A more granular table (`trade_pnl`) that stores individual Flex rows for deeper analysis.

## 9. Next Decisions Before Coding

When you pick this up again, key choices to confirm:

1. **Tables for phase 1**
   - Definitely `daily_pnl` as described.
   - Decide whether to also build `daily_symbol_pnl` now or later.
2. **Postgres runtime**
   - Confirm whether you want to use Docker or a local service.
3. **Implementation order**
   - Likely:
     1. Implement `apps/data/db.py` (connection + `ensure_schema` for `daily_pnl`).
     2. Implement `apps/data/ingest_flex.py` for historical backfill.
     3. Implement `apps/data/ingest_ib.py` using `IBClient` to update today’s P&L.
     4. Add a tiny notebook or FastAPI endpoint to query `daily_pnl` for charts.

Once you’re ready, we can turn this design into code, step by step, without duplicating existing logic. 

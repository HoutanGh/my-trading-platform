# Daily P&L Storage and Calendar (Apps)

This document describes the **current** P&L ingestion and calendar architecture.

## 1. Executive Summary

- We **import a broker CSV** (Flex report) and store **daily realized P&L** in Postgres.
- The **CLI** triggers ingestion; the **API** serves read‑only data; the **web UI** renders a calendar.
- The calendar flow is **Web → API → Core → DB**, with ingestion writing the data the calendar reads.
- The `apps/` package is the single source of truth; `apps_archive/` is legacy only.

## 2. End‑to‑End Daily P&L Calendar Flow (Detailed)

### 2.1 Write path: ingestion (what populates the calendar)
1. Run the CLI command `ingest-flex` with `csv=...` and `account=...`.
2. `apps/core/pnl/service.py::PnlService.ingest_flex` publishes `PnlIngestStarted`.
3. `apps/adapters/pnl/flex_ingest.py::FlexCsvPnlIngestor.ingest`:
   - Ensures the `daily_pnl` table exists.
   - Reads the CSV with pandas.
   - Requires columns `TradeDate` and `FifoPnlRealized`.
   - Coerces types, drops invalid rows, aggregates by `TradeDate`.
   - Upserts each day into `daily_pnl` keyed by `(account, trade_date, source)`.
4. The service publishes `PnlIngestFinished` (or `PnlIngestFailed` on error).

### 2.2 Read path: calendar (what the user sees)
1. The web UI (`web/src/App.tsx`) picks the current month in **UTC** and builds
   `start_date` and `end_date` covering the whole month.
2. It calls `GET /pnl/daily?account=...&start_date=...&end_date=...` on the API
   (`VITE_API_BASE_URL` or `http://localhost:8000` by default).
3. `apps/api/routes/pnl.py` validates the dates (end ≥ start), then calls
   `PnlService.get_daily_pnl`.
4. `apps/adapters/pnl/store.py::PostgresDailyPnlStore.fetch_daily_pnl` runs a
   date‑bounded query and returns rows ordered by `trade_date`.
5. The API returns JSON rows (`account`, `trade_date`, `realized_pnl`, `source`).
6. The UI maps rows by `trade_date`, computes **monthly total**, **weekly totals**,
   and renders a color‑coded day cell for each date.

## 3. Architecture at a Glance

Simple flow:

CSV file
  → CLI command (ingest‑flex)
  → PnL service (apps core)
  → CSV adapter + DB adapter (apps adapters)
  → Postgres table `daily_pnl`
  → API `/pnl/daily`
  → Web calendar UI

Layering (from inside to outside):

- **Core (apps/core)**: the business rules and “what should happen.”
- **Adapters (apps/adapters)**: the “how” (CSV parsing, Postgres writes).
- **API (apps/api)**: HTTP interface for the web.
- **CLI (apps/cli)**: command‑line interface for developers.
- **Web (web/)**: read‑only UI that calls the API.

## 4. Data Model

### Table: `daily_pnl`

- `id BIGSERIAL PRIMARY KEY`
- `account TEXT NOT NULL` – IB account id (e.g. `DU123456`).
- `trade_date DATE NOT NULL` – P&L date.
- `realized_pnl DOUBLE PRECISION NOT NULL` – realized P&L for that day.
- `source TEXT NOT NULL` – usually `"flex"`.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `UNIQUE (account, trade_date, source)`

Notes:
- No `unrealized_pnl` column yet.
- `source` lets us reconcile multiple sources later if needed.

## 5. Flex CSV Ingestion (Current)

Input: Flex CSV similar to `data/raw/Daily_PL.csv` with columns:
- `TradeDate`
- `FifoPnlRealized`

Flow:
1. CLI runs `ingest-flex` with a CSV path + account.
2. CSV is parsed with pandas.
3. Dates + numbers are cleaned.
4. P&L is summed per trade date.
5. Each day is upserted into `daily_pnl`.

CLI example:
- `python -m apps.cli`
- `ingest-flex csv=data/raw/Daily_PL.csv account=DU123456`

## 6. API (Read‑Only)

- The API exposes **GET `/pnl/daily`** for the calendar.
- On startup, `ensure_schema()` creates the `daily_pnl` table if needed.
- Input validation: if `end_date < start_date`, return HTTP 400.
- Response is a list of JSON objects with:
  - `account` (string)
  - `trade_date` (ISO date string, `YYYY-MM-DD`)
  - `realized_pnl` (number)
  - `source` (string)

Example API call:
- `/pnl/daily?account=DU123456&start_date=2024-01-01&end_date=2024-01-31`

## 7. Web Calendar Behavior (Current)

- **Default account**: from `VITE_DEFAULT_ACCOUNT` or `"paper"`.
- **API base URL**: from `VITE_API_BASE_URL` or `http://localhost:8000`.
- **Empty account**: if the account input is blank, the UI skips the fetch.
- **UTC month boundaries**: `start_date` is the 1st of the month in UTC,
  `end_date` is the last day of the month in UTC.
- **Date mapping**: each `trade_date` is mapped to a day cell by ISO string.
- **Totals**:
  - Monthly total = sum of `realized_pnl` across returned rows.
  - Weekly totals computed from daily values and the month’s starting weekday.
- **Trades optional**: the UI can show `trade_count` or `trades` if present, but
  the current API does not return these fields.

## 8. Logging and Events

We use the existing event bus for observability.

Core events:
- `PnlIngestStarted`
- `PnlIngestFinished`
- `PnlIngestFailed`

Outputs:
- Console output in the CLI.
- JSONL logs in `apps/journal/events.jsonl` (via `JsonlEventLogger`).
- Adapter logs include CSV stats (rows read, days aggregated).

This gives both high‑level “what happened” and low‑level “why it happened.”

## 9. Relevant File Tree (Current)

```
docs/
  daily_pnl_design.md
data/
  raw/
    Daily_PL.csv
apps/
  core/
    pnl/
      models.py
      ports.py
      service.py
      events.py
  adapters/
    pnl/
      db.py
      store.py
      flex_ingest.py
    eventbus/
      in_process.py
    logging/
      jsonl_logger.py
  api/
    main.py
    deps.py
    routes/
      pnl.py
    settings.py
  cli/
    __main__.py
    repl.py
  journal/
    events.jsonl
web/
  src/
    App.tsx
    App.css
```

## 10. Setup (Quick Start)

1) Postgres
- Run a local Postgres instance and create a database.
- Set `DATABASE_URL`.

2) Ingest CSV
- Run the apps CLI and ingest a Flex CSV:
  - `python -m apps.cli`
  - `ingest-flex csv=data/raw/Daily_PL.csv account=DU123456`

3) API
- Start FastAPI:
  - `uvicorn apps.api.main:app --reload`

4) Web
- Start Vite:
  - `cd web && npm run dev`

## 11. Planned Extensions (Not Implemented)

- Per‑symbol daily P&L table (`daily_symbol_pnl`).
- IB API ingestion for “today only.”
- Richer analytics (trade‑level drill‑down).

This design keeps apps as the single source of truth while allowing the UI to stay simple and read‑only.

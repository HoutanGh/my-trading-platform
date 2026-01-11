# Daily P&L Storage and Calendar (Apps)

This document describes the **current** P&L ingestion and calendar architecture.

## 1. Executive Summary

- We **import a broker CSV** (Flex report) and store **daily realized P&L** in Postgres.
- The **CLI** triggers ingestion; the **API** serves read‑only data; the **web UI** shows a calendar.
- The `apps/` package is the single source of truth; `apps_archive/` is legacy only.

## 2. Architecture at a Glance

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

## 3. Data Model

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

## 4. Flex CSV Ingestion (Current)

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

## 5. API and Web (Read‑Only)

- The API exposes **GET `/pnl/daily`** for the calendar.
- The web app calls the API and renders a monthly view.

Example API call:
- `/pnl/daily?account=DU123456&start_date=2024-01-01&end_date=2024-01-31`

## 6. Logging and Events

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

## 7. File Map (Current)

Core:
- `apps/core/pnl/models.py` – shared data shapes (e.g., `DailyPnlRow`).
- `apps/core/pnl/events.py` – ingest lifecycle events.
- `apps/core/pnl/ports.py` – interfaces the core expects.
- `apps/core/pnl/service.py` – orchestration (ingest + query).

Adapters:
- `apps/adapters/pnl/db.py` – DB connection + `ensure_schema()`.
- `apps/adapters/pnl/store.py` – upsert + query helpers.
- `apps/adapters/pnl/flex_ingest.py` – CSV parsing + aggregation.

API:
- `apps/api/main.py` – FastAPI entrypoint + CORS.
- `apps/api/routes/pnl.py` – `/pnl/daily` endpoint.
- `apps/api/deps.py` – service wiring for routes.

CLI:
- `apps/cli/repl.py` – `ingest-flex` command.
- `apps/cli/__main__.py` – wires services + event bus.

Web:
- `web/src/App.tsx` – calendar UI that calls `/pnl/daily`.

## 8. Setup (Quick Start)

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

## 9. Planned Extensions (Not Implemented)

- Per‑symbol daily P&L table (`daily_symbol_pnl`).
- IB API ingestion for “today only.”
- Richer analytics (trade‑level drill‑down).

This design keeps apps as the single source of truth while allowing the UI to stay simple and read‑only.

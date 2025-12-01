# Async Refactor Plan (ib_insync + asyncio)

This document describes how to refactor the current ib_insync‑based CLI + breakout watcher into a clean, fully async architecture using `asyncio`, while adapting existing code and keeping the current structure as much as possible.

---

## Goal

- Refactor the current ib_insync‑based CLI + breakout watcher into a fully async architecture using `asyncio`.
- **Keep** the existing structure and files as much as possible.
- **Adapt** existing functions instead of creating brand new ones, unless necessary.
- Explain each refactor step briefly before showing code.

---

## What exists already (important)

- A CLI loop that uses something like:

  ```python
  while True:
      cmd = input("> ")
      ...
  ```

- A “Breakout live” entry in the CLI (e.g. a function like `_breakout_live`) that currently calls a **blocking** breakout watcher.
- A breakout watcher function that’s written as a blocking loop (`while True` + `time.sleep` / `ib.sleep` + checks).
- A module that handles IB connection (using `ib_insync`).

---

## Do NOT

- Do **not** redesign the whole project structure.
- Do **not** create lots of new top‑level files unless absolutely needed.
- Prefer refactoring existing functions and modules.

---

## Important constraints about ib_insync + async

- ib_insync exposes both sync and async APIs.
- Async methods end with `Async` and must be awaited, for example:
  - `await ib.connectAsync(host, port, clientId=...)`
  - `await ib.reqHistoricalDataAsync(...)`
- Once we go async, for the new async path we should:
  - Use **only** the `Async` methods (`connectAsync`, `reqHistoricalDataAsync`, etc.).
  - Avoid `IB.run()`, `IB.sleep()`, and `util.run()` in this architecture.
  - Avoid blocking calls like `time.sleep()` and `input()`.
  - Use `await asyncio.sleep(...)` for delays.
  - Use an async or non‑blocking way to read from stdin (e.g. `aioconsole.ainput()` or `asyncio.to_thread(input, ...)`).

---

## Target architecture

- **One** `asyncio` event loop (e.g. `asyncio.run(main())`) that runs:
  - the IB connection,
  - one or more breakout watcher tasks,
  - an async CLI loop for user commands.
- Breakout watchers should be **event‑driven**:
  - Subscribe once (real‑time bars or tick updates).
  - Maintain state as new bars arrive.
  - Detect breakout patterns.
  - Never rely on tight `while True` + `time.sleep` loops.

---

## Refactor steps (adapt existing code)

### Step 1 – Identify and refactor entrypoint

- Find the current main entrypoint (where the CLI is started and IB is connected).
- Refactor it into an async entrypoint. In our case we updated the existing `run()` in `apps/api/cli.py` to be `async def run():` and to call `await ib.connectAsync(...)`, and the `if __name__ == "__main__":` block now does `asyncio.run(run())`.

  ```python
  async def main():
      ib = IB()
      await ib.connectAsync(...)
      ...
  ```

- Show the **updated version** of this existing function (do not invent a brand new `main` if one already exists).
- Explain in 3–5 sentences what changed (e.g. moved to `async`, using `connectAsync`, using `asyncio.run(main())` at the bottom).
  - We converted the prior synchronous `run()` into an async coroutine.
  - We now instantiate `IB()` and `await ib.connectAsync(...)` with the same env-driven host/port/clientId.
  - The rest of the menu loop stays the same for now, but runs inside the async function.
  - The module entrypoint uses `asyncio.run(run())` so the event loop is created once and drives the CLI + IB connection.

### Step 2 – Adapt IB usage to async

- Find where the code currently calls `ib.connect()`, `ib.reqHistoricalData()`, or other blocking ib_insync calls.
- For the paths used by the CLI:
  - Manual path is already async: `run()` uses `connectAsync`, and `place_buy_bracket_pct_async` relies on `IBClient` async helpers.
  - Convert the remaining sync IB calls (breakout watcher, ingest scripts, cancels/positions if used in async contexts) to their `Async` variants and `await` them.
- Show only the relevant changed functions.
- Explain briefly what you changed and why (e.g. “this prevents blocking the event loop when fetching data”).

### Step 3 – Refactor the breakout watcher

- Locate the current breakout watcher implementation (`apps/engine/breakout_watcher.py`).
- Refactor it to be cooperative and event‑driven:
  - Replace `reqHistoricalData` + `ib.sleep` loop with `reqHistoricalDataAsync` (or a streaming bar subscription) and async callbacks.
  - Remove `while True` + `time.sleep`/`ib.sleep`; process new bars as they arrive.
- Keep the breakout detection logic similar to the existing one, just make it non‑blocking.
- Show a **diff‑style refactor** of that function, not a brand‑new file.
- Briefly explain how the watcher now runs “in the background” on the event loop instead of blocking.

### Step 4 – Refactor the CLI loop to async

- The manual loop already uses `asyncio.to_thread(input, "> ")` and awaits async service calls; finish the rest:
  - Wrap menu input and breakout paths so they don’t block.
  - On “Breakout live”, create an `asyncio` task for the async watcher (`asyncio.create_task(...)`).
- Ensure the CLI loop itself does **not** block the event loop.
- Show the updated CLI function(s) with minimal structural changes.

### Step 5 – Wire everything together

- Show how the async entrypoint (`run` in `apps/api/cli.py`) now:
  - Creates the IB client and connects via `await ib.connectAsync(...)` (already done),
  - Starts the CLI loop task,
  - Awaits it (and any other long‑running tasks) using `asyncio.gather` or similar.
- Ensure there is a clean shutdown path:
  - On a quit/exit command:
    - Cancel breakout watcher tasks.
    - Disconnect IB (`await ib.disconnectAsync()` or appropriate shutdown).
    - Exit gracefully.

---

## General style guidelines for the refactor

- At each step:
  - **First** explain in plain language what you’re about to change.
  - **Then** show the updated code for existing functions (diff‑style where possible).
- Keep changes local and readable so it’s easy to follow and learn from them.
- Prefer minor, targeted refactors over big rewrites.
- Do **not** introduce unnecessary new modules or top‑level files unless strictly needed by the async design.

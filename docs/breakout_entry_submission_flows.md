# Breakout Entry Submission Flows and Gaps

This document focuses on one slice of breakout automation: from trigger detection to entry order submission visibility.

## Scope

In scope:
- Trigger evaluation in breakout runner.
- Transition from `BreakoutConfirmed` to order submission events (`OrderIntent`, `OrderSent`, `OrderIdAssigned`, `OrderStatusChanged`).
- Ladder entry path because current RENX confusion happened there.

Out of scope:
- Post-entry TP/SL management details after successful submission.

## Core Components in This Slice

- Strategy runner: `apps/core/strategies/breakout/runner.py`
- Order service: `apps/core/orders/service.py`
- IBKR adapter: `apps/adapters/broker/ibkr_order_port.py`
- Event log: `apps/journal/events.jsonl`

## Event Contract (Expected Entry Chain)

For a successful submit attempt, expected chain is:

1. `BreakoutBreakDetected`
2. `BreakoutConfirmed`
3. `OrderIntent`
4. `OrderSent`
5. `OrderIdAssigned`
6. `OrderStatusChanged` (typically `PendingSubmit` first)
7. `BreakoutStopped` with reason `order_submitted` or `order_submitted_fast`

Notes:
- `OrderIntent` is emitted in core service before IB adapter call (`apps/core/orders/service.py:55`).
- `OrderSent` and `OrderIdAssigned` are emitted inside IB adapter after `placeOrder` (`apps/adapters/broker/ibkr_order_port.py:251`, `apps/adapters/broker/ibkr_order_port.py:255`, `apps/adapters/broker/ibkr_order_port.py:350`).
- `BreakoutStopped` is emitted by runner only after submit call returns (`apps/core/strategies/breakout/runner.py:258`, `apps/core/strategies/breakout/runner.py:287`).

## Flow 1: Slow Watcher Happy Path

1. Slow stream emits a bar that triggers `BreakoutAction.ENTER`.
2. `_watch_slow` calls `_submit_entry` (`apps/core/strategies/breakout/runner.py:317`).
3. `_submit_entry` emits `BreakoutConfirmed` (`apps/core/strategies/breakout/runner.py:145`).
4. Quote checks pass for limit entry (`apps/core/strategies/breakout/runner.py:157`).
5. Runner calls `order_service.submit_ladder` (`apps/core/strategies/breakout/runner.py:258`).
6. Service emits `OrderIntent` (`apps/core/orders/service.py:59`).
7. IB adapter places parent order and emits `OrderSent`, then `OrderIdAssigned` (`apps/adapters/broker/ibkr_order_port.py:251`, `apps/adapters/broker/ibkr_order_port.py:255`, `apps/adapters/broker/ibkr_order_port.py:350`).
8. Runner emits `BreakoutStopped(reason=order_submitted)` after call returns (`apps/core/strategies/breakout/runner.py:289`).

## Flow 2: Fast Watcher Happy Path

1. Fast stream meets thresholds (`apps/core/strategies/breakout/runner.py:360`).
2. `_watch_fast` calls `_submit_entry(... reason="order_submitted_fast")` (`apps/core/strategies/breakout/runner.py:363`).
3. Same submission sequence as Flow 1.
4. Runner emits `BreakoutStopped(reason=order_submitted_fast)`.

## Flow 3: Confirmed but Quote-Rejected (No OrderIntent)

1. `_submit_entry` emits `BreakoutConfirmed` before quote validation (`apps/core/strategies/breakout/runner.py:145`).
2. Quote check fails (`quote_missing` or `quote_stale`) (`apps/core/strategies/breakout/runner.py:170`, `apps/core/strategies/breakout/runner.py:214`).
3. Runner emits `BreakoutRejected` + `BreakoutStopped`.
4. No `OrderIntent`, because order service is never called.

Implication:
- `BreakoutConfirmed` does not guarantee an order submit attempt. It only means trigger confirmation.

## Flow 4: Submitted Then Broker Reject/Inactive

1. `OrderIntent` + `OrderSent` + `OrderIdAssigned` appear.
2. `OrderStatusChanged` moves to `Inactive`/`Cancelled`.
3. IB gateway usually has matching rejection details (for example code `201` in `apps/journal/ib_gateway.jsonl`).

Example symbols in logs:
- `XHLD`, `CIGL`, `JZXN` have `Inactive`/`Cancelled` states in `apps/journal/events.jsonl`.

Implication:
- Submission happened, but broker/account/product constraints rejected activation.

## Flow 5: Observed RENX Gap (Confirmed + Intent, No Sent/IdAssigned)

Observed sequence for `breakout:RENX:0.31`:
- `BreakoutConfirmed` (`apps/journal/events.jsonl:6804`)
- `OrderIntent` (`apps/journal/events.jsonl:6805`)
- No matching `OrderSent`/`OrderIdAssigned`/`OrderStatusChanged` after that.

Comparison run (`breakout:RENX:0.28`) shows full chain:
- `OrderIntent` -> `OrderSent` -> `OrderIdAssigned` -> `OrderStatusChanged` (`apps/journal/events.jsonl:5665`, `apps/journal/events.jsonl:5666`, `apps/journal/events.jsonl:5667`, `apps/journal/events.jsonl:5668`).

Most likely mechanism:

1. `_submit_entry` sets `decision` very early (`apps/core/strategies/breakout/runner.py:129`).
2. Fast watcher exits when it sees `decision.is_set()` (`apps/core/strategies/breakout/runner.py:358`).
3. Main task waits for first completed watcher and cancels the other (`apps/core/strategies/breakout/runner.py:382`, `apps/core/strategies/breakout/runner.py:383`).
4. If fast watcher completes first while slow watcher is still inside submit path, slow watcher can be cancelled before adapter emits `OrderSent`.

## Gaps Identified

1. Race window between `decision.set()` and completion of submit call.
- The winning watcher can cause cancellation of the submitting watcher.

2. No explicit event for submission failure in runner.
- `_submit_entry` does not emit `BreakoutStopped(reason=submit_failed)` or equivalent when submit raises.

3. No invariant monitor for dangling intents.
- There is no automatic alert for `OrderIntent` without `OrderSent`/`OrderIdAssigned` within a timeout.

4. Background breakout task failures are not journaled as structured events.
- `_on_breakout_done` prints exceptions but does not publish a dedicated ops/event entry (`apps/cli/repl.py:1768`, `apps/cli/repl.py:1781`).

5. `BreakoutConfirmed` semantics are broad.
- It means breakout condition confirmed, not that order reached broker submission state.

## Practical Log Triage Checklist

For any breakout `client_tag`:

1. Confirm `BreakoutConfirmed` exists.
2. Check for `OrderIntent`.
3. Check for `OrderSent`.
4. Check for `OrderIdAssigned`.
5. Check for `OrderStatusChanged` progression.
6. If missing after step 2, inspect watcher cancellation/failure timing and terminal output.
7. If step 4 exists but status is `Inactive`/`Cancelled`, inspect `apps/journal/ib_gateway.jsonl` for reject reason codes.

## Current Hypothesis for RENX 0.31 Incident

The incident is consistent with a submission-cancellation race:
- submit attempt started (`OrderIntent` exists),
- but adapter-level submission events did not occur,
- leaving position at zero and no broker order lifecycle in logs.

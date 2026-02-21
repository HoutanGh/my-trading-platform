# Breakout Reliability Prompt (Session Consolidation)

Use this as a copy-paste prompt for future work on the breakout flow.

## Prompt

You are working in this repo: `/home/houtang/GitHub/my-trading-platform`.

Goal:
- Stabilize breakout order lifecycle and make broker-vs-app behavior unambiguous.
- Focus on small, safe changes with strong observability.

Constraints:
- Smallest correct change only.
- Core/Ports/Adapters boundary must hold (core must not import adapters).
- Default safety posture is paper trading.
- Prefer broker-truth for positions/open orders over local assumptions.
- Add verification steps for every proposed change.

Known issues from recent sessions:
1. Ladder TP behavior confusion:
   - TP1 is expected to be size-split (ex: 600/300/100 for qty=1000), but logs often make it look like full-size behavior.
   - In ATCH (`breakout:ATCH:0.27`), child statuses show `Filled` while some `filled_qty/avg_fill_price` fields are `0`, causing ambiguity.
2. Immediate full-exit scenarios:
   - All TP children are submitted at once.
   - If TP levels are already marketable vs actual entry, multiple TP legs can fill nearly instantly.
3. Stop lifecycle confusion:
   - Need clear distinction between stop-filled vs stop-cancelled due to OCA after TP activity.
   - Stop resize logic recently changed to in-place modify; verify no regressions.
4. Submission race history:
   - Dual watcher behavior previously risked `OrderIntent` without `OrderSent` when task cancellation hit in-flight submission.
5. Reconnect/orphan risk:
   - Active exit orders can survive while position is zero (or vice versa); reconciliation must surface this clearly.
6. CLI audit mismatch:
   - `trades` output can be misleading when events show `status=Filled` but qty fields are zero/contradictory.

What to deliver:
1. A concise root-cause matrix:
   - confirmed behavior
   - logging artifact
   - unresolved unknown
2. A minimal patch plan ordered by risk reduction.
3. A file-level change map (exact files).
4. Verification plan:
   - local commands
   - broker-side checks
   - expected event signatures.

High-priority outcomes:
- Execution-level truth for child fills (not only orderStatus snapshots).
- Clear bracket invariants (parent fill vs child cumulative fill).
- Reliable post-reconnect reconciliation events.
- CLI output that does not overstate fills when qty is zero/unknown.

Repository map (where each concern lives):
- Breakout decision flow:
  - `apps/core/strategies/breakout/runner.py`
  - `apps/core/strategies/breakout/logic.py`
  - `apps/core/strategies/breakout/events.py`
- Broker order submission + child handling:
  - `apps/adapters/broker/ibkr_order_port.py`
  - `apps/adapters/broker/ibkr_connection.py`
- Order validation + domain events:
  - `apps/core/orders/models.py`
  - `apps/core/orders/service.py`
  - `apps/core/orders/events.py`
- Active/open-order broker truth:
  - `apps/core/active_orders/*`
  - `apps/adapters/broker/ibkr_active_orders_port.py`
- CLI orchestration + diagnostics:
  - `apps/cli/repl.py`
  - `apps/cli/event_printer.py`
  - `apps/cli/position_origin_tracker.py`
- Logs and docs:
  - `apps/journal/events.jsonl`
  - `apps/journal/ib_gateway.jsonl`
  - `apps/journal/ops.jsonl`
  - `docs/breakout_automation.md`
  - `docs/breakout_entry_submission_flows.md`

Suggested analysis workflow:
1. Reconstruct one incident end-to-end by `client_tag` and order IDs.
2. Separate broker-confirmed facts from app-inferred facts.
3. Identify where state is sourced from `orderStatus` vs execution callbacks.
4. Propose minimum instrumentation to close the ambiguity.
5. Only then propose behavior changes.

## JDZG RCA Snapshot (2026-02-12)

Incident:
- `client_tag=breakout:JDZG:2`
- Operator-observed broker truth (`orders broker`): TP children at `2.1` and `2.2` both showed `qty=100` (not `70/30`).

Broker truth policy for this case:
- Treat `orders broker` as the source of truth for active order quantity.
- Use app events as secondary evidence only.

Confirmed facts:
- Parent entry intended and filled `qty=100` (`OrderIntent` / `OrderFilled`).
- App child events report intended split quantities (`take_profit_1 qty=70`, `take_profit_2 qty=30`).
- Despite that, broker-side active order view showed both TP children as `100`.
- Local IB client behavior is pass-through for order size:
  - `LimitOrder(action, totalQuantity, lmtPrice, ...)` writes `order.totalQuantity`.
  - `Client.placeOrder` serializes and sends `order.totalQuantity` directly.

Most likely root cause:
- Effective child order sizing at the broker did not match intended ladder split.
- This is consistent with attached/bracket semantics where child sizing can behave as parent-sized exits unless explicitly modeled and validated as independent partial exits.

Critical observability gap:
- Current child event payloads can mask broker-effective size because they use app-expected qty fields.
- Child fill logging clamps filled quantity to expected child qty, which can make logs look like `70/30` even when broker-effective child quantity is larger.

Why this caused fast/full exits:
- If TP1 is marketable and broker-effective size is `100`, TP1 can flatten the full position immediately.
- TP2 then appears as pending-cancel/cancelled via OCA flow.

Session handoff checklist (for next debugging session):
1. Capture raw broker child order payload (`orderId`, `parentId`, `orderType`, `totalQuantity`, `lmtPrice`, `auxPrice`, `status`) at submit time and on every child status change.
2. Emit an explicit mismatch event when `broker_child.totalQuantity != intended_tp_qty`.
3. Preserve raw broker fill shares (unclamped) alongside any normalized/app-interpreted fill fields.
4. Verify a paper test where intended split is `70/30` and assert broker open orders are exactly `70` and `30`.
5. If broker still returns `100/100`, redesign exit architecture to submit independent partial exits instead of parent-attached children for ladder targets.

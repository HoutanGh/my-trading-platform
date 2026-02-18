# BENF and Child-Order Fix Notes

Date: 2026-02-18
Scope: detached 70/30 breakout child order reliability

## BENF context

- Breakout children were rejected with:
  - `Order rejected - reason:The contract is not available for short sale. (201)`
- A stale BENF orphan exit order had already been detected as open (`PreSubmitted`) before the later breakout attempt.
- Orphan reconciliation was running in warn-only mode, so stale exits were detected but not cancelled automatically.

## Child-order fixing priorities

1. Enable orphan auto-cancel in runtime
   - Set `APPS_ORPHAN_EXIT_ACTION=cancel`
   - Keep `APPS_ORPHAN_EXIT_SCOPE=all_clients`

2. Add pre-entry symbol cleanup gate
   - Before arming new detached exits, cancel stale open SELL exits for the same `account+symbol`.
   - Wait for confirmation they are no longer active, otherwise fail safe.

3. Add child sell-exposure guard
   - Compute available inventory after subtracting reserved qty from other active SELL exits.
   - If available qty is insufficient, do not post new child exits.

4. Debounce child incident handling for `202` cancel races
   - Treat immediate cancel/inactive as transitional for a short delay.
   - Re-check paired TP/stop fill state before escalating to incident handling.

5. Reconcile child intent after submit/replace
   - If broker qty/attributes do not match intended state, cancel and recreate promptly.

6. Make protection state strict
   - Mark `protected` only when a valid stop is confirmed accepted.
   - Keep `unprotected` when emergency stop cannot be accepted.

## Operational verification checklist

1. On connect, verify no stale symbol exits are left open:
   - `orders broker scope=all_clients account=DUH631912`
2. Run one detached70 breakout with two TPs.
3. Confirm:
   - no `201` on valid close-side exits,
   - TP2 remains active when TP1 fills (unless explicit strategy logic says otherwise),
   - protection state transitions match actual open stop protection.

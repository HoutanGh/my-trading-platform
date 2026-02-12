# Breakout Flow Checklist

Issue tags (mapped from `docs/prompt_breakout_reliability_session.md` + `docs/breakout_automation.md`):
- `[I1]` Ladder TP behavior confusion (status/fill ambiguity).
- `[I2]` Immediate full-exit when multiple TP legs are marketable.
- `[I3]` Stop lifecycle ambiguity (filled vs OCA-cancelled).
- `[I4]` Submission race (`OrderIntent` without `OrderSent`).
- `[I5]` Reconnect/orphan exits vs actual position truth.
- `[I6]` CLI audit mismatch (`Filled` state with zero/unknown qty fields).
- `[I7]` TP reprice partial/skip ambiguity (sequential replace, timeout, no explicit skip/failure event).

1. CLI accepts command only after prechecks pass (connected, breakout configured, `level > 0`, and quote source present for `entry=limit`), then creates task (`Breakout watcher started: breakout:SYMBOL:LEVEL`).
2. `BreakoutStarted` is logged with level/TP/SL config.
3. Bar subscriptions start (`BarStreamStarted` for slow + fast stream if enabled).
4. Break condition appears (`BreakoutBreakDetected`).
5. Entry is confirmed (`BreakoutConfirmed`).
6. Quote gate passes for limit entry (no `BreakoutRejected` for `quote_missing/quote_stale`).
7. `OrderIntent` is emitted (core decided to submit). **[I4 hotspot]**
8. `OrderSent` is emitted (adapter called IB placeOrder). **[I4 hotspot]**
9. Parent order gets `OrderIdAssigned` and `OrderStatusChanged` (PreSubmitted/Submitted/Filled path).
10. Parent fill happens (`OrderFilled` with filled qty/avg price). **[I1, I2, I6 hotspot]**
11. Child orders appear (`BracketChildOrderStatusChanged` for TP1/TP2/TP3/SL). **[I1, I2, I6, I7 hotspot]**
12. Runtime management runs (TP fills, SL resize/replace events, protection-state events). **[I1, I2, I3, I6, I7 hotspot]**
13. Watcher ends cleanly with `BreakoutStopped` (`order_submitted` / `order_submitted_fast` / other reason). **[I4 secondary check]**
14. CLI task callback shows final watcher state (`finished`, `cancelled`, or `failed`). **[I6 hotspot]**
15. Final broker truth matches logs (positions + active orders reconciliation). **[I1, I5, I6, I7 hotspot]**



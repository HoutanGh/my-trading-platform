# Breakout Flow Checklist

1. CLI accepts command only after prechecks pass (connected, breakout configured, `level > 0`, and quote source present for `entry=limit`), then creates task (`Breakout watcher started: breakout:SYMBOL:LEVEL`).
2. `BreakoutStarted` is logged with level/TP/SL config.
3. Bar subscriptions start (`BarStreamStarted` for slow + fast stream if enabled).
4. Break condition appears (`BreakoutBreakDetected`).
5. Entry is confirmed (`BreakoutConfirmed`).
6. Quote gate passes for limit entry (no `BreakoutRejected` for `quote_missing/quote_stale`).
7. `OrderIntent` is emitted (core decided to submit).
8. `OrderSent` is emitted (adapter called IB placeOrder).
9. Parent order gets `OrderIdAssigned` and `OrderStatusChanged` (PreSubmitted/Submitted/Filled path).
10. Parent fill happens (`OrderFilled` with filled qty/avg price).
11. Child orders appear (`BracketChildOrderStatusChanged` for TP1/TP2/TP3/SL).
12. Runtime management runs (TP fills, SL resize/replace events, protection-state events).
13. Watcher ends cleanly with `BreakoutStopped` (`order_submitted` / `order_submitted_fast` / other reason).
14. CLI task callback shows final watcher state (`finished`, `cancelled`, or `failed`).
15. Final broker truth matches logs (positions + active orders reconciliation).

# RIME Oversell RCA (2026-02-12)

## Scope
- Symbol: `RIME`
- Primary incident: broker-side net short shown in CLI (`positions`), interpreted as automation oversell.
- Reference logs:
  - `apps/journal/events.jsonl`
  - `apps/journal/ib_gateway.jsonl`

## Broker-Truth Principle
- Broker state is authoritative (`orders broker`, `positions`).
- App events are diagnostic and can be incomplete or delayed relative to broker execution.

## What Happened (Evidence Timeline)

### 0) Watcher behavior: repeated starts, not a duplicate active watcher
- CLI breakout watcher key is `breakout:{symbol}:{level}` (`apps/cli/repl.py:1228`), and launch refuses duplicate active key (`apps/cli/repl.py:1236` to `apps/cli/repl.py:1237`).
- For `RIME`, logs show multiple watcher starts over time:
  - `events.jsonl:7974` (`RIME` level `1.3`)
  - `events.jsonl:8006` (`RIME` level `1.3`)
  - `events.jsonl:8039` (`RIME` level `1.25`)
- So this incident is consistent with repeated watcher runs and multiple parent orders, not two identical active watchers running concurrently under the same key.
- There is still an event gap: one early `RIME:1.3` run has start/confirm/order events but no corresponding `BreakoutStopped`, while later runs do (`events.jsonl:8016`, `events.jsonl:8049`).

### 1) Multiple RIME breakout entries were opened close together
- Parent buy #679 (`breakout:RIME:1.3`) filled 100: `events.jsonl:7990`.
- Parent buy #693 (`breakout:RIME:1.3`) filled 100: `events.jsonl:8026`.
- Parent buy #705 (`breakout:RIME:1.25`) filled 100: `events.jsonl:8059`.

This created 3 separate parent positions (+300 total) with overlapping detached child exits.

### 2) Parent #693: stop filled, then TP legs also filled (same parent)
- Stop #694 filled 100 at 1.25: `events.jsonl:8034`.
- After that, TP1 #695 filled 70: `events.jsonl:8072`.
- After that, TP2 #696 filled 30: `events.jsonl:8075`.

Implication: this parent executed 200 shares of SELL against a 100-share BUY (oversell on this parent by 100).

### 3) Parent #705: TP legs filled first, then stop also filled full size
- TP1 #707 filled 70 at 1.3: `events.jsonl:8064`.
- TP2 #708 filled 30 at 1.32: `events.jsonl:8069`.
- Later, stop #706 filled 100 at avg 1.21: `events.jsonl:8080`.

Implication: this parent also executed 200 SELL vs 100 BUY (oversell on this parent by 100).

### 4) Outside-RTH stop-limit behavior triggered
- IB gateway warning for RIME stop-limit capping: code `2161`, `events.jsonl:8077`.
- Followed by `Cannot modify a filled order` on stop #706: code `104`, `events.jsonl:8081`.

This confirms the stop was elected/filling while adjustment/cancel logic could not be applied in time.

### 5) Missing lifecycle evidence on first parent (#679)
- For parent #679 children (#680/#681/#682), logs show creation/submission only (`events.jsonl:7981` to `events.jsonl:7995`) and no later fill/cancel events.
- This is an observability gap: broker may have progressed those orders without corresponding app events in this log.

## Quantity Reconciliation From Logged Fills

### Logged BUY fills (RIME, 2026-02-12)
- #679: +100 (`events.jsonl:7990`)
- #693: +100 (`events.jsonl:8026`)
- #705: +100 (`events.jsonl:8059`)
- Logged total BUY: `+300`

### Logged SELL fills (RIME, 2026-02-12)
- #694 stop: -100 (`events.jsonl:8034`)
- #695 TP1: -70 (`events.jsonl:8072`)
- #696 TP2: -30 (`events.jsonl:8075`)
- #707 TP1: -70 (`events.jsonl:8064`)
- #708 TP2: -30 (`events.jsonl:8069`)
- #706 stop: -100 (`events.jsonl:8080`)
- Logged total SELL: `-400`

Net from logged fills: `-100` shares.

## Why This Still Does Not Match Your Broker Snapshot (`-270`)
- Your broker snapshot (`positions`) reported a larger short (`-270`) and is the source of truth.
- Logs therefore under-represent at least part of broker execution, likely due one or more of:
  - missing child order lifecycle events for some working children (notably parent #679 path),
  - session/disconnect/re-subscribe timing gaps,
  - overlapping parent+child states not fully captured by the current event model.

## Most Likely Failure Mode
1. Detached exits were live at broker for overlapping parents.
2. Stop and TP legs were not reliably made mutually exclusive at runtime (cancel/replace not consistently winning the race).
3. As price moved, both legs could execute for the same parent.
4. Repeated across parents, this created net short exposure (oversell).

## Log Gaps That Matter for RCA
- No complete terminal state for every child order of parent #679.
- No end-of-parent invariant log (e.g., `parent_buy_filled_qty` vs `sum_child_sell_filled_qty`).
- No broker reconciliation snapshot tied to each parent after every child fill.

## Bottom Line
- The logs prove at least two concrete double-execution paths (parents #693 and #705) where SELL notional exceeded parent BUY notional.
- Your broker-reported `RIME` short confirms the oversell was real and larger than what app logs alone can fully reconstruct.

# Breakout Automation — Layering Refactor Plan

> Created: 2026-02-21
> Status: Planning — no code changes yet
> Prerequisite reading: [breakout_automation.md](breakout_automation.md)

---

## 1. Problem statement

The breakout automation workflow has ~2,350 lines of domain/business logic leaked into the adapter and CLI layers. The Core/Ports/Adapters architecture requires: **core decides, adapter executes, CLI parses + displays**.

| Layer | File | Total lines | Domain logic lines | % leaked |
|---|---|---|---|---|
| Adapter | `apps/adapters/broker/ibkr_order_port.py` | 3,161 | ~1,500 | ~47% |
| CLI | `apps/cli/repl.py` | 4,208 | ~700 | ~17% |
| CLI | `apps/cli/event_printer.py` | 1,922 | ~150 | ~8% |

### What's already in core (small)
- `apps/core/strategies/breakout/policy.py` (167 lines) — mode parsing, ratio splits, validation
- `apps/core/orders/detached_ladder.py` (72 lines) — 2 tiny pure functions: `collect_detached_reprice_decisions()` and `select_detached_incident_pair()`
- `apps/core/strategies/breakout/runner.py` (492 lines) — streaming strategy + entry submission
- `apps/core/orders/service.py` (310 lines) — order validation + submit/replace/cancel

---

## 2. Scope: the runtime workflow (what matters)

Only the **live execution path** is in scope. The runtime workflow is:

1. **CLI parses input** → builds breakout config (mode, TPs, SL, qty splits)
2. **Runner starts** → subscribes to bar stream + quote stream
3. **Trigger fires** → bar close >= level
4. **Runner submits entry** → market or limit-at-ask via order service
5. **Entry fills** → runner submits exits:
   - **attached**: bracket (1 TP + 1 SL via OCA)
   - **detached70**: 2 TP + 2 SL (two OCA pairs)
   - **detached**: 3 TP + 3 SL (three OCA pairs)
6. **Detached state machine runs** → tracks fills, manages protection state:
   - TP fills → milestone check → reprice remaining SLs
   - SL fills → cross-cancel paired TP
   - Incidents → calculate uncovered qty → emergency stop or flatten
7. **All legs filled or cancelled** → watcher stops (single-fire)

### Out of scope (separate concerns, defer)
- `_cmd_trades` session reconstruction + analytics + PnL reporting (post-hoc CLI display, not runtime)
- `event_printer.py` narrative session tracking (display-only)
- `_breakout_outcome_kind`, `_compute_pnl`, `_session_entry_is_fully_filled` (reporting helpers)
- `is_tp_kind` / `is_stop_kind` / `leg_from_kind` / `breakout_leg_alias` (naming convention helpers, used for display)

---

## 3. Where the leaks are (by workflow step)

### Step 1: Config assembly (CLI → core)

**Location**: `apps/cli/repl.py` `_cmd_breakout` (L957–1348, ~390 lines)

The CLI orchestrates the full config assembly pipeline instead of calling a single core function:

- **Mode inference routing**: "tp-2 implies 2-TP auto mode", "2 TPs with no explicit mode → infer detached70"
- **Default TP count policy**: `tp_count` defaults to 3 when `tp=auto` and no count given
- **Auto-TP orchestration**: validate levels increasing + above SL, compute ratios, split qty
- **70/30 allocation enforcement**: hardcoded `tp_alloc` must be exactly 70-30 for detached70
- **Mode-specific qty derivation**: different `mode_validation_qtys` for DETACHED_70_30

Also in `_deserialize_breakout_config` (L3499–3588): domain invariants (TP count must be 2 or 3, qtys sum to total, mutual exclusivity) during config resume from persisted state.

**Note**: Individual policy functions (ratios, splits, validation) are already delegated to `core/strategies/breakout/policy.py` via wrappers. The issue is the **orchestration** — the pipeline that decides what to compute, in what order, with what defaults.

### Steps 5–6: Detached state machine (Adapter → core)

**Location**: `apps/adapters/broker/ibkr_order_port.py` (~1,500 lines of domain logic)

Two near-duplicate methods containing identical state machines:

| Method | Lines | Coverage |
|---|---|---|
| `_submit_ladder_order_detached_70_30` | L446–L1196 (~750) | 2-pair (detached70) |
| `_submit_ladder_order_detached_3_pairs` | L1198–L1997 (~800) | 3-pair (detached) |

Each one contains **all of this inline**:

| Domain concern | Example location (70/30 variant) |
|---|---|
| **Fill-tracking state machine** — `tp_filled_qty`, `tp_completed`, `stop_filled`, execution dedup, qty capping, completion detection | L605–L612 (state setup), L1064–L1091 (`_on_tp_fill`) |
| **Milestone-based stop repricing** — "after TP1 fills → reprice SL2 and SL3" | L614–L626 (milestone config), L998–L1008 (`_maybe_schedule_reprices_locked`) |
| **Protection state transitions** — "are we protected or unprotected?" | L694–L713 (`_emit_protection_state_locked`) |
| **Cross-cancel on stop fill** — "stop filled → cancel paired TP" | L1093–L1124 (`_on_stop_fill`) |
| **Incident recovery** — cancel children → check position → calculate uncovered qty → arm emergency stop | L757–L833 (`_handle_child_incident`) |
| **Emergency flatten policy** — `if paper_only: flatten_position_market(...)` | L831–L838 |
| **OCA pair naming/grouping** — which legs go in which OCA | L473 |
| **TP allocation / qty split setup** | L477–L494 |

Other adapter methods with domain logic:

| Method | Lines | Issue |
|---|---|---|
| `_reprice_pair2_stop` / `_reprice_single_pair_stop` | L870–997, L1645–1790 (~220) | Full stop reprice workflow with incident fallback |
| `_attach_stop_trigger_reprice` | L2927–3019 (~80) | Detects stop-limit trigger (PreSubmitted→Submitted), decides to reprice to touch price |
| `submit_ladder_order` / `_submit_ladder_order_detached` | L331–398 (~45) | Execution mode routing/dispatch |
| `_outside_rth_for_session_phase` / `_uses_stop_limit` / `_outside_rth_stop_limit_price` | scattered (~20) | Session/trading policy rules |

### Step 6 (also): Orphan reconciliation (CLI → core)

**Location**: `apps/cli/repl.py` `_reconcile_orphan_exit_orders` (L2442–2566)

Orphan detection rules (SELL + has parent + zero position = orphan) and auto-cancel orchestration are implemented inline. The equivalent `_reconcile_detached_protection_coverage` correctly delegates to core — but this one doesn't.

---

## 4. Target file layout

### Breakout-specific logic → `core/strategies/breakout/`

```
apps/core/strategies/breakout/
├── __init__.py              # existing
├── events.py                # existing — lifecycle events
├── logic.py                 # existing — trigger logic
├── policy.py                # existing — mode/ratio/validation (extend with config assembly)
├── runner.py                # existing — streaming strategy + entry
├── config.py                # NEW — build_breakout_config(), from_dict()
├── detached.py              # NEW — unified N-pair state machine
│                            #        (absorbs core/orders/detached_ladder.py)
└── reconciliation.py        # NEW — orphan exit detection rules
```

### Generic logic → `core/orders/` or existing modules

| Logic | Destination | Why generic |
|---|---|---|
| Stop-limit trigger detection + reprice-to-touch policy | `core/orders/stop_policy.py` | Any stop-limit order, not breakout-specific |
| Session phase → outsideRTH mapping | `core/orders/session.py` | Any order placed during ETH |
| Stop-limit buffer % calculation | Same | Generic stop-limit pricing |
| Fill delta tracking (cumulative→delta) | `core/orders/fills.py` | Any fill stream |
| OCA group naming/creation helpers | `core/orders/models.py` (extend) | Any multi-leg order |

---

## 5. Execution plan

### Phase 1: Config assembly → core (low risk, no adapter changes)

Extract the config assembly pipeline from CLI into core.

| Step | What | From | To |
|---|---|---|---|
| 1a | `build_breakout_config(raw_inputs) → BreakoutRunConfig` | repl.py `_cmd_breakout` L1073–1305 | `core/strategies/breakout/config.py` |
| 1b | `BreakoutRunConfig.from_dict(payload)` | repl.py `_deserialize_breakout_config` L3499–3588 | Same file |
| 1c | Orphan exit detection rule | repl.py L2484–2502 | `core/strategies/breakout/reconciliation.py` |

**After Phase 1**: CLI calls `build_breakout_config()` and `from_dict()` — one-liner delegation. All existing policy wrappers collapse. Testable with unit tests.

### Phase 2: Unify the two detached state machines (adapter-internal refactor)

Before extracting to core, **merge the 2-pair and 3-pair duplicates** into one parameterized implementation inside the adapter.

| Step | What | ~Lines saved |
|---|---|---|
| 2a | Extract shared inner functions (`_on_tp_fill`, `_on_stop_fill`, `_emit_protection_state_locked`, `_maybe_schedule_reprices_locked`, `_handle_child_incident`) into a single set parameterized by `n_pairs` | ~600 |

**Why before extraction**: moving 1,320 lines of duplicated code into core creates a bigger mess. Deduplicate first, then the extraction target is ~700 lines of unified logic.

### Phase 3: Extract unified detached state machine → core

| Step | What | From | To |
|---|---|---|---|
| 3a | `DetachedLadderStateMachine` — fill tracking, completion detection, protection state, milestone repricing decisions, cross-cancel decisions | adapter (unified) | `core/strategies/breakout/detached.py` |
| 3b | Incident recovery decisions (uncovered qty calculation, emergency flatten policy) | adapter | Same file |
| 3c | Stop trigger detection + reprice-to-touch policy | adapter L2927–3019 | `core/orders/stop_policy.py` |
| 3d | Session phase → outsideRTH, stop-limit buffer calculation | adapter scattered | `core/orders/session.py` |

**Target pattern**: Adapter calls `state_machine.on_tp_fill(pair, qty)` → gets back a `Decision` dataclass (reprice these stops at these prices, cancel this TP, emit this event) → adapter executes the IB API calls.

### Phase 4 (deferred): CLI analytics extraction

Not part of the runtime workflow. Can be done independently later:

- `_cmd_trades` session reconstruction → `core/analytics/trades.py`
- PnL, outcome classification, fill tracking helpers
- `event_printer.py` narrative session tracking → consume shared core tracker
- `is_tp_kind` / `leg_from_kind` / `breakout_leg_alias` duplication cleanup

---

## 6. Testing strategy

| Phase | Test approach |
|---|---|
| Phase 1 | Unit tests for `build_breakout_config()` and `from_dict()` — cover mode inference, qty splits, defaults, validation errors. Existing characterization tests in `tests/cli/test_breakout_config_characterization.py` should still pass. |
| Phase 2 | Existing behavior must be identical — run full test suite + manual breakout test on paper. This is a pure refactor (dedup), no behavior change. |
| Phase 3 | Unit tests for `DetachedLadderStateMachine` — test state transitions directly: TP fill → reprice decision, SL fill → cancel decision, incident → coverage calculation. These are the tests that don't exist today because the logic is buried in adapter closures. |
| Phase 4 | Unit tests for trade reconstruction + PnL computation. |

---

## 7. Key design decisions

1. **Start breakout-specific, promote to generic later**: The detached state machine starts in `core/strategies/breakout/detached.py`. If a second strategy needs ladders, promote to `core/orders/`. Avoids premature generalization.

2. **Decision dataclass pattern for adapter boundary**: Core returns `Decision` objects (what to do), adapter executes them (how to do it via IB API). This keeps async/broker concerns out of core.

3. **Deduplicate before extracting**: The 2-pair/3-pair duplication must be merged first. Extracting duplicated code just moves the problem.

4. **Config assembly as a single core entry point**: Replace the CLI's ~150-line orchestration pipeline with one `build_breakout_config()` call that owns all defaults, inference, and validation.

---

## 8. Risk notes

- **Phase 2 is the riskiest** — merging two 750+ line methods with subtle differences in a live trading adapter. Must be done carefully with paper testing.
- **Phase 3 changes the adapter's control flow** — the adapter currently uses inline closures that capture mutable state. Extracting to a state machine changes how state flows. Needs careful async/locking review.
- **No adapter-level integration tests exist** for detached execution race paths (fast partial fills, reject/cancel timing). This is a gap that should be addressed during or after Phase 3.

# Iteration 02 — Final Report

## A. Executive result

The execution chain is **safer but not fully trustworthy end-to-end**. Iteration 02 closed two proven P0 holes: every mapped `strategy_combine` broker order now crosses a final fail-closed `Engine.post()` mode boundary, and a broker API read failure is no longer treated like a confirmed empty portfolio. Tests prove paper mode creates zero fake-broker order calls. However, durable intent/submitted/fill state and independent broker order-status confirmation do not exist, so crash/timeout replay safety remains unresolved P0 debt.

## B. Execution graph

Mapped production paths are in `execution_graph.md`:

1. **Entry:** supervisor → Engine.tick → broker snapshot/reconcile → process_slot signal → duplicate checks → `RiskManager.approve_entry` → `Engine.post` → broker response → analytics/local state → final reconcile.
2. **Normal exit:** local SL/TP/time condition → `Engine.post` → response → analytics/local close → reconcile.
3. **Forced exit:** eject/auto-swap close path → `force_close_slot` → `Engine.post` → confirmed response → local close/removal flow.
4. **Pending swap retry:** Iteration 01 universe VETO → only valid pending can reach force-close; then Iteration 02 final mode boundary applies.

The only production `post_order` call inside `strategy_combine` is `core/engine.py:Engine.post()`.

## C. Risk boundary

Regular new entries cannot reach `post()` without `RiskManager.approve_entry()` returning truthy `ok`; a VETO returns before broker call. Signal computation occurs inside `Engine.process_slot`, not as a direct strategy-to-broker call.

**Limit:** risk result is a tuple rather than a durable structured risk-decision artifact; exception handling fail-closes the current slot invocation, but there is no end-to-end persisted signal/risk/intention trace. This remains P1/P0 recovery debt, not silently claimed safe.

## D. Mode boundary

**Fixed P0 defect:** before Iteration 02, `Engine.post()` sent `post_order` without inspecting `mode` or `paper_first`.

Now the final order boundary permits broker submission only when:

```text
mode == "live" AND paper_first == false
```

All other states return `VETO:EXECUTION_NOT_AUTHORIZED` before calling the client.

Current configuration is `mode=paper`, `paper_first=true`. Fake-client runtime proof: `broker_post_calls=0`.

## E. Duplicate-order safety

Existing protections:

- supervisor nonblocking `flock` prevents concurrent supervisor ticks;
- engine blocks duplicate local slot position and same-ticker open position;
- portfolio JSON writes atomically via temporary file + replace.

Changed:

- entry path now derives deterministic `intent_id` material and passes it as broker request order-id for the logical entry action.

**Limit:** the system does not persist an `intent/submitted/confirmed` journal or query broker order state after a timeout. Broker acceptance semantics for repeated order-id are not proven. Therefore full restart/ambiguous-response deduplication is **unresolved P0**.

## F. Broker vs local truth

Broker hierarchy is explicit:

```text
broker reality > portfolio.json operational state > analytics.db/reporting
```

- confirmed broker position missing locally: adopted to a matching slot;
- confirmed empty broker `{}`: local ghost position cleared;
- broker direction/quantity mismatch: local fields corrected;
- broker API error: now `None`, no destructive reconciliation and new entries VETO `broker_state_unknown`.

**Limit:** broker positions not mapped to configured specs are skipped; broker fills/operations are not independently used to repair missing analytics after a crash.

## G. Analytics truth

Canonical local DB: `/root/prop-desk/strategy_combine/analytics.db` via `core.analytics.DB_PATH`.

`state/analytics.db` is 0 bytes and is not authoritative. Iteration 02 corrected `code/validate_allocator_dryrun.py` to read the root DB.

## H. Failure / crash behavior

| Window | Result |
|---|---|
| Paper-mode order attempt | final VETO, zero broker call |
| Broker snapshot API error | `None`; skip reconciliation; new entry VETO |
| Confirmed empty broker | reconcile local ghost slot clear |
| Broker open rejected (`executed=0`) | no local open write |
| Broker close rejected (`executed=0`) | local open position retained |
| Broker accepts, then process dies before local write | broker truth can later be adopted, but provenance/duplicate safety incomplete |
| Post order timeout/unknown response | no broker order-state recovery; unresolved P0 |
| Crash between analytics and portfolio writes | not transactional; reconciliation detects position mismatch but analytics repair incomplete |

## I. Defects found

| Severity | Defect | Status |
|---|---|---|
| P0 | `Engine.post()` had no mode/paper-first hard boundary | **FIXED** |
| P0 | broker read error collapsed into empty-state semantics / could permit entry without known snapshot | **FIXED** |
| P0 | no durable order intent/submitted/fill state; ambiguous response/restart replay unproven | OPEN — TD-016 |
| P1 | analytics writes and portfolio JSON are not one transaction | OPEN |
| P1 | broker operations/order-status not used for post-crash analytics recovery | OPEN |
| P2 | empty `state/analytics.db` could mislead allocator dry-run | **FIXED** |

## J. Changes implemented

- `core/config.py`: `CombineConfig.paper_first` exposed from runtime config.
- `core/engine.py`:
  - final `Engine.post()` hard gate;
  - broker fetch returns `None` on error;
  - reconciliation distinguishes `{}` from `None`;
  - new entries fail closed when broker state unknown;
  - stable entry intent-id material passed to broker request.
- `code/validate_allocator_dryrun.py`: root canonical analytics path.
- `tests/test_execution_truth.py`: fake-broker/fault injection tests.
- `code/test_audit_fixes.py`: fake post compatibility and stale-threshold test correction.

## K. Tests

Passed:

```text
31 passed
```

Focused suites:

- `tests/test_execution_truth.py`
- `tests/test_swap_universe_gate.py`
- `tests/test_config_safety.py`
- `tests/test_live_safety_regressions.py`

Also passed:

```text
python3 code/test_audit_fixes.py
python3 code/test_reconcile.py
python3 -m py_compile core/config.py core/engine.py core/supervisor.py code/validate_allocator_dryrun.py
```

## L. Runtime verification

Verified without real order:

```text
mode=paper
paper_first=true
open_positions=0
open_analytics_trades=0
new analytics orders during Iteration 02=0
state/analytics.db=0 bytes
```

A fake client that would raise on `post_order` received no call; final result was `VETO:EXECUTION_NOT_AUTHORIZED`.

No active execution-like process outside `combine-*` units was found. `combine-*` services point to supervisor/seeder/data-downloader. Host-level Tinkoff scripts outside `strategy_combine` exist but were inactive and out of scope.

## M. Safety confirmation

- broker orders created during Iteration 02: **NO**;
- broker positions intentionally changed: **NO**;
- `mode` changed: **NO**;
- `paper_first` changed: **NO**;
- strategy semantics changed: **NO**;
- risk limits changed: **NO**.

## N. Mission Control updates

Updated:

- `01_SYSTEM_MAP.md`
- `02_SOURCE_OF_TRUTH.md`
- `08_TECH_DEBT_REGISTER.md`
- `09_ROADMAP.md`
- `10_MATURITY_MODEL.md`

Evidence bundle:

```text
docs/mission_control/reviews/ITERATION-02/
├── execution_graph.md
├── broker_call_inventory.md
├── source_of_truth_audit.md
├── failure_matrix.md
├── changed_files.md
├── tests.md
├── runtime_verification.md
└── final_report.md
```

No ADR was created: the final execution boundary and canonical analytics path are narrow safety repairs, not a complete execution architecture decision. TD-016 records the material architecture that requires a future ADR.

## O. Remaining P0 risks

1. No durable intent/submitted/confirmed order state: timeout/crash after broker acceptance can leave broker ahead of local state and replay safety is unproven.
2. No broker order-status/operations confirmation after ambiguous post response.
3. Broker positions that cannot be mapped to configured specs are skipped, so unknown external broker state may remain invisible to local reconciliation.

## P. Recommended next task

**Exactly one bounded task:** create a design-only ADR and testable state contract for a durable `order_intent → submitted → broker_confirmed` journal, including timeout/restart recovery rules. Do not implement it in that task.

**Iteration 02 stops here.**

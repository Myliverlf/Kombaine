# Iteration 02 — Tests

## Focused pytest regression suite

Command:

```bash
python3 -m pytest \
  tests/test_execution_truth.py \
  tests/test_swap_universe_gate.py \
  tests/test_config_safety.py \
  tests/test_live_safety_regressions.py -q
```

**Result:** `31 passed`.

## Legacy/core focused checks

Commands:

```bash
python3 code/test_audit_fixes.py
python3 code/test_reconcile.py
python3 -m py_compile core/config.py core/engine.py core/supervisor.py code/validate_allocator_dryrun.py
```

**Result:** all completed successfully.

`test_audit_fixes.py` printed its expected corrupt-JSON recovery diagnostic inside a temporary fixture, then passed all checks.

## Required coverage mapping

| Required concern | Evidence |
|---|---|
| T1 signal cannot reach broker without risk | `Engine.process_slot()` code path + risk VETO behavior; final fake-broker boundary tests |
| T2 failed/invalid risk fails closed | entry code returns before `post()`; slot exception does not proceed; PARTIALLY VERIFIED structured verdict absent |
| T3 paper mode | fake broker sees zero calls for paper/dryrun/backtest/live+paper_first=true |
| T4 duplicate intent identity | deterministic input produces same `intent_id`; broker duplicate semantics unproven |
| T5 ambiguous response | documented P0 debt; no blind retry code exists, but no order-state confirmation |
| T6 failed open | `executed=0` causes no local open write; code path covered by structure/review |
| T7 failed close | `executed=0` preserves local position; code path reviewed |
| T8 mismatch | `code/test_reconcile.py` covers adopt/clear/qty/direction/mixed cases |
| T9 canonical DB | path test plus allocator path correction |
| T10 restart/recovery | documented gap: no durable intent state; no false claim of proof |
| T11 Iteration 01 | `tests/test_swap_universe_gate.py`: 10 passed |
| T12 allowed normal flow | core audit fixture entry flow and reconciliation tests pass; explicit live not enabled |

## Fault injection

- fake broker `post_order` tracks calls and is asserted zero under paper configuration;
- broker position fetch throws exception → `None`, no destructive reconciliation/new entry;
- confirmed empty snapshot `{}` → reconciliation clears local stale state;
- foreign/unknown swap candidate → universe VETO before Engine;
- test fake execution validates stable entry-id material.

No real broker calls were used by the test suite.

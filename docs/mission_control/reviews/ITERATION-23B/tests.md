# Tests — Iteration 23B
**Date:** 2026-08-30

## New Tests: T1–T30
File: `tests/test_live_preconditions_23b.py`
58 tests covering all Iteration 23B requirements.

| Test | Description | Tests | Status |
|------|------------|-------|--------|
| T1 | Full-account capital base semantics | 4 | PASS |
| T2 | Full capital != forced full exposure | 3 | PASS |
| T3 | No leverage | 4 | PASS |
| T4 | Pilot gross exposure <= 10% | 2 | PASS |
| T5 | Planned risk <= 0.25% equity | 1 | PASS |
| T6 | Per-strategy open risk <= 0.50% | 1 | PASS |
| T7 | Total open risk <= 0.50% | 1 | PASS |
| T8 | Daily 1% halt | 2 | PASS |
| T9 | Weekly 2% halt | 2 | PASS |
| T10 | Drawdown 5% halt | 2 | PASS |
| T11 | No averaging down | 1 | PASS |
| T12 | No pyramiding pilot | 1 | PASS |
| T13 | One live position pilot | 1 | PASS |
| T14 | Strategy cannot loosen global limits | 2 | PASS |
| T15 | Allocation cannot bypass Risk Gate | 1 | PASS |
| T16 | Missing risk boundary blocks | 1 | PASS |
| T17 | GAZP/LKOH/SBER tier policy | 2 | PASS |
| T18 | BR/Si restriction respected | 1 | PASS |
| T19 | Extra instrument cannot auto-enter LIVE | 1 | PASS |
| T20 | Broker read-only guard | 2 | PASS |
| T21 | Broker mutation denied | 2 | PASS |
| T22 | Account equity authoritative | 3 | PASS |
| T23 | Reconciliation | 3 | PASS |
| T24 | Position state not assumed | 2 | PASS |
| T25 | Invalid lot => no trade | 1 | PASS |
| T26 | Stale data blocks | 2 | PASS |
| T27 | Stale strategy blocks | 2 | PASS |
| T28 | Human-only authorization | 1 | PASS |
| T29 | LIVE_EXECUTE remains denied | 3 | PASS |
| T30 | Full regression marker | 4 | PASS |
| **Total** | | **58** | **ALL PASS** |

## Full Regression
```
2155 passed
1 skipped (test_data_loader)
1 flaky (test_concurrent_decisions_one_wins — pre-existing SQLite threading)
0 new regressions
```

## Iteration 23 Tests Updated
File: `tests/test_live_preconditions.py`
- T8BrokerSnapshot: Updated to accept nested broker_truth format
- All other tests: PASS (unchanged)

# Tests — Iteration 23

**Date:** 2026-08-30

## Test Results: T1-T30

| Test | Description | Result |
|------|-------------|--------|
| T1 | paper/paper_first invariant | PASS (4/4) |
| T2 | broker read-only allowlist | PASS (3/3) |
| T3 | mutating broker method denied | PASS (2/2) |
| T4 | exact-scope data certification | PASS (7/7) |
| T5 | missing data cannot be fabricated | PASS (2/2) |
| T6 | instrument restrictions respected | PASS (2/2) |
| T7 | broker account identity | PASS (3/3) |
| T8 | broker snapshot | PASS (2/2) |
| T9 | reconciliation | PASS (2/2) |
| T10 | UNKNOWN broker truth blocks | PASS (2/2) |
| T11 | position state not assumed | PASS (1/1) |
| T12 | FX scope logic | PASS (2/2) |
| T13 | stale strategy blocks | PASS (2/2) |
| T14 | zero eligible does not weaken threshold | PASS (2/2) |
| T15 | execution path no bypass | PASS (2/2) |
| T16 | duplicate-order ambiguity blocks | PASS (2/2) |
| T17 | permission separation | PASS (2/2) |
| T18 | LIVE_EXECUTE denied | PASS (2/2) |
| T19 | HUMAN-only live authorization | PASS (2/2) |
| T20 | agent/system cannot authorize | PASS (2/2) |
| T21 | authorization exact binding | PASS (1/1) |
| T22 | stale authorization invalid | PASS (2/2) |
| T23 | risk cap cannot be invented | PASS (3/3) |
| T24 | abort does not auto-liquidate | PASS (3/3) |
| T25 | snapshot immutable | PASS (3/3) |
| T26 | hard blocker respected | PASS (2/2) |
| T27 | READY does not enable LIVE | PASS (2/2) |
| T28 | Telegram cannot authorize | PASS (1/1) |
| T29 | MC cannot authorize | PASS (1/1) |
| T30 | full regression | PASS (4/4) |

## Summary
- **Total tests:** 70 (T1-T30 with sub-tests)
- **PASS:** 70
- **FAIL:** 0
- **ERROR:** 0
- **SKIP:** 0

## Full Regression
- Existing tests: 2029 collected
- New tests: 70 (T1-T30)
- Total: 2099 collected
- All GREEN

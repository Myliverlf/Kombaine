# Tests — Iteration 23C
**Date:** 2026-08-30

## New Tests: T1-T30

File: tests/test_live_qualification_23c.py
41 tests covering all Iteration 23C requirements.

| Test | Description | Tests | Status |
|------|------------|-------|--------|
| T1 | GAZP/SBER qualification scope | 3 | PASS |
| T2 | LKOH/BR/Si excluded from live scope | 3 | PASS |
| T3 | External LKOH untouched | 2 | PASS |
| T4 | Immutable campaign identity | 3 | PASS |
| T5 | Duplicate suppression | 2 | PASS |
| T6 | Family diversity | 2 | PASS |
| T7 | Thresholds unchanged | 2 | PASS |
| T8 | Authoritative costs | 2 | PASS |
| T9 | Walk-forward no leakage | 2 | PASS |
| T10 | Parameter robustness | 1 | PASS |
| T11 | Temporal robustness | 1 | PASS |
| T12 | Regime provenance | 2 | PASS |
| T13 | Risk boundary required | 2 | PASS |
| T14 | 0.25% risk cap | 1 | PASS |
| T15 | 10% exposure cap | 1 | PASS |
| T16 | Valid lot sizing | 1 | PASS |
| T17 | No leverage | 1 | PASS |
| T18 | PAPER evidence cannot be fabricated | 2 | PASS |
| T19 | Maturity cannot skip | 2 | PASS |
| T20 | Rejected evidence persisted | 1 | PASS |
| T21 | Multiple-testing diagnostic | 1 | PASS |
| T22 | Leakage invalidation | 1 | PASS |
| T23 | Ranking cannot approve | 1 | PASS |
| T24 | Agent cannot live-authorize | 1 | PASS |
| T25 | Zero candidates valid | 1 | PASS |
| T26 | Crash/resume | 1 | PASS |
| T27 | No duplicate evidence after resume | 1 | PASS |
| T28 | Resource guard | 1 | PASS |
| T29 | Deterministic G4 | 1 | PASS |
| T30 | Full regression | 2 | PASS |
| **Total** | | **41** | **ALL PASS** |

## Full Regression

```
23B baseline: 2155 passed, 1 skipped, 1 flaky
23C new:      41 passed
Total:        2196 passed, 1 skipped, 1 flaky
New regressions: 0
```

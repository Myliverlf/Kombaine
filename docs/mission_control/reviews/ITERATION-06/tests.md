# Tests — Iteration 06

**Date:** 2026-08-29 20:10 UTC
**Total tests in test_seeder_handoff.py:** 45
**Result:** ALL PASS

## Mandatory Tests (T1–T16)

| ID | Test | Status |
|----|------|--------|
| T1 | latest COMPLETED resolution | PASS |
| T2 | RUNNING cannot seed | PASS |
| T3 | FAILED/PARTIAL/BLOCKED cannot seed | PASS |
| T4 | integrity failure blocks | PASS |
| T5 | cross-run contamination rejected | PASS |
| T6 | missing config_key rejected | PASS |
| T7 | foreign universe rejected | PASS |
| T8 | broken canonical + valid legacy no silent fallback | PASS |
| T9 | legacy requires explicit opt-in and labeling | PASS |
| T10 | same run twice is idempotent | PASS |
| T11 | new completed run provides new candidates | PASS |
| T12 | registry remains lifecycle truth | PASS |
| T13 | signal_pool/waitlist remain registry-derived | PASS |
| T14 | mid-write failure cannot corrupt registry | PASS |
| T15 | scheduler resolves canonical source | PASS |
| T16 | Iterations 01–05 regressions green | PASS |

## Failure Matrix (F1–F18)

| ID | Scenario | Status |
|----|----------|--------|
| F1 | latest pointer missing | PASS |
| F2 | pointer malformed | PASS |
| F3 | pointer → missing run | PASS |
| F4 | manifest missing | PASS |
| F5 | status RUNNING | PASS |
| F6 | PARTIAL/FAILED/BLOCKED | PASS |
| F7 | integrity failed | PASS |
| F8 | eligible artifact missing | PASS |
| F9 | eligible artifact corrupt | PASS |
| F10 | candidate run_id mismatch | PASS |
| F11 | missing config_key | PASS |
| F12 | duplicate config_key | PASS |
| F13 | foreign universe | PASS |
| F14 | same run twice | PASS |
| F15 | legacy valid, canonical valid | PASS |
| F16 | canonical invalid, legacy valid | PASS |
| F17 | newer replaces older | PASS |
| F18 | registry write fails midway | PASS |

## Additional Edge Cases (9 tests)

| Test | Status |
|------|--------|
| validation returns structured result | PASS |
| seed result structured | PASS |
| dry run no registry mutation | PASS |
| multiple candidates mixed validity | PASS |
| legacy fallback blocked when no scan | PASS |
| run_seeder_handoff legacy opt-in | PASS |
| run_seeder_handoff legacy no scan_path | PASS |
| empty eligible list | PASS |
| eligible not a list | PASS |
| pointer not a dict | PASS |
| pointer missing run_id | PASS |

## Regression

- Full pytest suite: 828 passed (pre-existing 1 failed + 10 errors unrelated)
- code/test_audit_fixes.py: ALL PASS
- code/test_reconcile.py: ALL PASS

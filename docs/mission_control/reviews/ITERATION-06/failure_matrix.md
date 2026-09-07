# Failure Matrix — Iteration 06

**Date:** 2026-08-29 20:10 UTC

## F1–F18 Results

| ID | Scenario | Seeding? | Registry Mutation? | Fallback? | Structured Status? | Recovery? | Test |
|----|----------|----------|-------------------|-----------|--------------------|-----------|----|
| F1 | latest pointer missing | NO | NO | NO SILENT | YES | create valid run | test_f1 |
| F2 | pointer malformed | NO | NO | NO SILENT | YES | fix pointer JSON | test_f2 |
| F3 | pointer → missing run | NO | NO | NO SILENT | YES | create run bundle | test_f3 |
| F4 | manifest missing | NO | NO | NO SILENT | YES | write manifest | test_f4 |
| F5 | status RUNNING | NO | NO | NO SILENT | YES | wait for completion | test_f5 |
| F6 | PARTIAL/FAILED/BLOCKED | NO | NO | NO SILENT | YES | fix/re-run | test_f6 |
| F7 | integrity failed | NO | NO | NO SILENT | YES | fix integrity, re-run | test_f7 |
| F8 | eligible artifact missing | NO | NO | NO SILENT | YES | write eligible file | test_f8 |
| F9 | eligible artifact corrupt | NO | NO | NO SILENT | YES | fix JSON, re-run | test_f9 |
| F10 | candidate run_id mismatch | PARTIAL | NO | NO SILENT | YES | fix candidate data | test_f10 |
| F11 | missing config_key | PARTIAL | NO | NO SILENT | YES | add config_key | test_f11 |
| F12 | duplicate config_key | PARTIAL | NO | NO SILENT | YES | deduplicate | test_f12 |
| F13 | foreign universe candidate | PARTIAL | NO | NO SILENT | YES | universe gate (It.01) | test_f13 |
| F14 | same run seeded twice | IDEMPOTENT | NO | N/A | YES | no-op | test_f14 |
| F15 | legacy valid, canonical valid | CANONICAL | YES | NO SILENT | YES | canonical wins | test_f15 |
| F16 | canonical invalid, legacy valid | NO | NO | NO SILENT | YES | fix canonical | test_f16 |
| F17 | newer COMPLETED replaces older | YES | YES | N/A | YES | pointer updates | test_f17 |
| F18 | registry write fails midway | PARTIAL | NO | NO SILENT | YES | retry or investigate | test_f18 |

## Hard Invariant Verification

```
INVALID CANONICAL INPUT ≠ SILENT LEGACY FALLBACK
→ Verified by: test_f1, test_f2, test_f3, test_f4, test_f8, test_f9, test_f16
→ All return HANDOFF_BLOCKED with structured blocked_reason
→ Legacy never auto-activates
```

# Tests — Iteration 07

**Date:** 2026-08-30
**Total tests in test_experiment_memory.py:** 58
**Result:** ALL PASS

## Mandatory Tests (T1–T17)

| ID | Test | Status |
|---|---|---|
| T1 | Deterministic family ID | PASS |
| T2 | Deterministic instance ID | PASS |
| T3 | Parameter ordering independence | PASS |
| T4 | Material parameter difference changes ID | PASS |
| T5 | EXACT_DUPLICATE classification | PASS |
| T6 | REVALIDATION classification | PASS |
| T7 | CODE_CHANGE classification | PASS |
| T8 | COST_MODEL_CHANGE classification | PASS |
| T9 | METHODOLOGY_CHANGE classification | PASS |
| T10 | INCOMPARABLE classification | PASS |
| T11 | Idempotent indexing (no duplicates) | PASS |
| T12 | Backfill restart safety | PASS |
| T13 | Provenance on index | PASS |
| T14 | Query API (history, config_key, recent) | PASS |
| T15 | Observation only (no side effects) | PASS |
| T16 | Classify API returns info, never commands | PASS |
| T17 | Non-COMPLETED run rejected | PASS |

## Failure Matrix (F1–F7)

| ID | Scenario | Status |
|---|---|---|
| F1 | Partial run rejected | PASS |
| F2 | Failed run rejected | PASS |
| F3 | Running run rejected | PASS |
| F4 | Missing manifest rejected | PASS |
| F5 | Missing ledger rejected | PASS |
| F6 | Corrupt JSONL skipped | PASS |
| F7 | Read-only DB rejected | PASS |

## Additional Edge Cases (34 tests)

Includes: no broker imports, valid classifications set, normalization, auto-creates DB, DB separation, schema version, exact duplicates across runs, revalidation with newer data, code/cost/validation change detection, legacy not indexed, missing manifest, read-only DB, partial backfill resumable, full lifecycle, normalization edge cases, hash determinism, canonical JSON.

## Regression

- Iteration 06 tests (test_seeder_handoff.py): ALL PASS
- Iteration 05 tests (test_run_contract.py): ALL PASS
- No existing tests broken by Iteration 07

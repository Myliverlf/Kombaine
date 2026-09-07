# Failure Matrix — Iteration 07

**Date:** 2026-08-30

## Mandatory Failure Scenarios (F1–F7)

| ID | Scenario | Expected | Result | Test |
|---|---|---|---|---|
| F1 | Partial run bundle (status != COMPLETED) | Reject indexing | PASS | test_partial_run_rejected |
| F2 | Failed run bundle | Reject indexing | PASS | test_failed_run_rejected |
| F3 | Running run bundle | Reject indexing | PASS | test_running_run_rejected |
| F4 | Missing manifest.json | Reject with error | PASS | test_missing_manifest |
| F5 | Missing ledger (candidates.jsonl) | Reject with error | PASS | test_missing_ledger |
| F6 | Corrupt JSONL line | Skip line, continue | PASS | test_corrupt_line_skipped |
| F7 | Read-only DB path | Reject with error | PASS | test_read_only_db_rejected |

## Classification Boundary Tests (T5–T10)

| ID | Scenario | Expected | Result | Test |
|---|---|---|---|---|
| T5 | Same instance exists | EXACT_DUPLICATE | PASS | test_exact_duplicate |
| T6 | Same family, newer data | REVALIDATION | PASS | test_revalidation_newer_data |
| T7 | Same family, different code | CODE_CHANGE | PASS | test_code_change |
| T8 | Same family, different cost | COST_MODEL_CHANGE | PASS | test_cost_change |
| T9 | Same family, different methodology | METHODOLOGY_CHANGE | PASS | test_methodology_change |
| T10 | Empty identity / no history | INCOMPARABLE | PASS | test_empty_ident_incomparable |

## Hard Invariant

```
NO CLASSIFICATION MAY ALTER EXPERIMENT EXECUTION
→ Verified by: T15 (classification_has_no_side_effects), T16 (classify_candidate_api_no_skip)
→ Memory is observation-only; Novelty Gate is a future iteration
```

## Additional Edge Cases

| Scenario | Result | Test |
|---|---|---|
| Legacy pre-canonical run not indexed | PASS | test_legacy_not_indexed |
| Idempotent double-index across runs | PASS | test_idempotent_double_index |
| Partial backfill resumable | PASS | test_partial_backfill_resumable |
| Full lifecycle: index → classify → query | PASS | test_full_lifecycle |

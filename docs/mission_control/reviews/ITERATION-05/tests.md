# Iteration 05 — Tests

## Test suite: `tests/test_run_contract.py`

**Total: 35 tests — 35 passed, 0 failed**

### T1–T14: Mandatory functional tests

| ID | Test | Status | What it verifies |
|----|------|--------|------------------|
| T1 | `test_t1_create_produces_unique_run_id` | ✅ | Unique run_id, run_dir created with logs/charts subdirs |
| T2 | `test_t2_start_planning_transitions_to_running` | ✅ | PLANNED→RUNNING, manifest populated with universe/timeframes/etc. |
| T3 | `test_t3_persist_plan_writes_research_plan_json` | ✅ | research_plan.json written, planned_configurations updated |
| T4 | `test_t4_append_candidate_writes_to_candidates_jsonl` | ✅ | Valid JSONL line with run_id, config_key, instrument |
| T5 | `test_t5_append_candidate_updates_manifest_counts` | ✅ | tested_configurations + failed_configurations correct |
| T6 | `test_t6_finalize_eligible_writes_eligible_candidates_json` | ✅ | eligible_candidates.json written, subset of ledger |
| T7 | `test_t7_finalize_report_writes_top10_and_report` | ✅ | top10.json and report.md written with correct content |
| T8 | `test_t8_complete_runs_integrity_and_sets_status` | ✅ | Full lifecycle → COMPLETED, manifest persisted |
| T9 | `test_t9_update_latest_pointer_only_for_completed` | ✅ | latest_run.json written with correct run_id |
| T10 | `test_t10_load_restores_state_from_disk` | ✅ | Load by run_id restores manifest and status |
| T11 | `test_t11_fingerprint_file_consistent` | ✅ | Same file → same 16-char SHA-256 |
| T12 | `test_t12_integrity_checks_produce_checks_json` | ✅ | checks.json with all 12 mandatory check names |
| T13 | `test_t13_fail_transitions_to_failed` | ✅ | RUNNING→FAILED with reason recorded |
| T14 | `test_t14_lock_acquire_release_lifecycle` | ✅ | Lock acquired, released, second run can acquire |

### F1–F12: Failure matrix

| ID | Test | Status | What it verifies |
|----|------|--------|------------------|
| F1 | `test_f1_invalid_state_transition_raises` | ✅ | PLANNED→COMPLETED raises ValueError |
| F2 | `test_f2_persist_plan_without_start_planning` | ✅ | persist_plan on PLANNED status doesn't crash |
| F3 | `test_f3_append_candidate_empty_still_writes` | ✅ | Empty dict produces valid entry with auto-config_key |
| F4 | `test_f4_complete_with_missing_artifacts_yields_partial` | ✅ | Missing plan → PARTIAL (not COMPLETED) |
| F5 | `test_f5_update_latest_pointer_on_non_completed_raises` | ✅ | RUNNING status → RuntimeError |
| F6 | `test_f6_load_nonexistent_run_raises` | ✅ | Non-existent run_id → FileNotFoundError |
| F7 | `test_f7_secret_in_run_dir_fails_no_secrets_check` | ✅ | api_key pattern → no_secrets_detected=False |
| F8 | `test_f8_multiple_append_candidate_are_append_safe` | ✅ | 10 appends → 10 valid JSONL lines |
| F9 | `test_f9_integrity_check_missing_plan_fails_plan_exists` | ✅ | No plan → plan_exists=False |
| F10 | `test_f10_integrity_check_missing_ledger_fails` | ✅ | No candidates → ledger_exists=False |
| F11 | `test_f11_planned_tested_reconcile_fails_when_count_mismatch` | ✅ | 2 of 5 planned → reconcile=False |
| F12 | `test_f12_read_latest_pointer_returns_none_when_absent` | ✅ | No pointer file → None |

### Additional edge-case tests

| Test | Status | What it verifies |
|------|--------|------------------|
| `test_generate_run_id_format` | ✅ | Format: prefix_YYYYMMDD_HHMMSS_8hex |
| `test_data_manifest_entry_with_valid_csv` | ✅ | Row count, hash, freshness captured |
| `test_data_manifest_entry_with_missing_file` | ✅ | row_count=0, hash=unavailable |
| `test_code_identity_captures_git_info` | ✅ | Returns git dict with file_hashes |
| `test_transition_blocked_states` | ✅ | BLOCKED/FAILED/COMPLETED have no outgoing transitions |
| `test_load_latest_eligible_no_pointer` | ✅ | Returns None when no pointer |
| `test_load_latest_eligible_with_pointer` | ✅ | Returns eligible data from completed run |
| `test_candidate_auto_generates_config_key` | ✅ | 16-char SHA-256 auto-generated |
| `test_manifest_persisted_after_every_mutation` | ✅ | manifest.json exists after each mutation step |

### Cross-iteration regression

All pre-existing test suites remain green:

| Test file | Tests | Status |
|-----------|-------|--------|
| `tests/test_execution_journal.py` | 15 | ✅ All pass |
| `tests/test_execution_truth.py` | 12 | ✅ All pass |
| `tests/test_swap_universe_gate.py` | 10 | ✅ All pass |
| `tests/test_config_safety.py` | 3 | ✅ All pass |
| `code/test_audit_fixes.py` | 9 | ✅ All pass |
| `code/test_reconcile.py` | 7 | ✅ All pass |

# Iteration 05 — Final Report

## A. Objective

Implement a Canonical Research Run Contract (`core/run_contract.py`) that
enforces immutable, reproducible, integrity-checked research runs for
strategy_combine. Every meaningful research cycle produces a run bundle
that is uniquely identified, lifecycle-tracked, persisted before heavy
execution, integrity-checked, and atomically promoted as latest only
after all checks pass.

## B. What was delivered

1. **`core/run_contract.py`** (924 lines) — Full lifecycle manager:
   - `ResearchRun` dataclass with create/load/fail/complete transitions
   - `generate_run_id()` collision-safe ID generation
   - `fingerprint_file()` SHA-256 data identity
   - `data_manifest_entry()` dataset fingerprinting
   - `code_identity()` + `capture_git_revision()` code provenance
   - `load_latest_eligible()` canonical handoff for seeder/registry
   - 12-point integrity check system (`run_integrity_checks()`)
   - Atomic latest pointer via `os.replace()`
   - fcntl.flock exclusive run lock with stale-PID recovery
   - Secret detection scan for run artifacts

2. **`tests/test_run_contract.py`** (35 tests) — Comprehensive test suite:
   - T1–T14: 14 mandatory functional tests
   - F1–F12: 12 failure matrix tests
   - 9 additional edge-case tests (format, data manifest, code identity,
     transition rules, pointer loading, auto-key generation, manifest
     persistence verification)

3. **`docs/mission_control/decisions/ADR-2026-08-29-canonical-research-run.md`**
   — Accepted ADR documenting the design rationale and trade-offs.

## C. Files changed

| File | Action | Lines |
|------|--------|-------|
| `core/run_contract.py` | **Created** | 924 |
| `tests/test_run_contract.py` | **Created** | ~570 |
| `docs/mission_control/decisions/ADR-2026-08-29-canonical-research-run.md` | **Created** | ~100 |

No existing files were modified. The contract is purely additive.

## D. Tests

```
python3 -m pytest tests/test_run_contract.py -v
```

**Result:** 35 passed, 0 failed (0.85s)

Covered:
- T1: Unique run_id and valid directory creation
- T2: PLANNED→RUNNING transition and manifest population
- T3: Plan persistence to research_plan.json
- T4: Candidate ledger append with valid JSONL
- T5: Manifest count updates (tested/failed)
- T6: Eligible candidates derivation and persistence
- T7: Report and top10 artifact writing
- T8: Full lifecycle → COMPLETED with integrity checks
- T9: Atomic latest pointer update for COMPLETED runs
- T10: Load/restore from disk by run_id
- T11: Fingerprint consistency
- T12: Integrity checks produce checks.json with all 12 checks
- T13: FAIL transition with reason
- T14: Lock acquire/release lifecycle
- F1: Invalid transition raises ValueError
- F2: persist_plan before start_planning
- F3: Empty candidate still produces valid entry
- F4: Missing artifacts → PARTIAL (not COMPLETED)
- F5: Latest pointer on non-COMPLETED raises RuntimeError
- F6: Load nonexistent → FileNotFoundError
- F7: Secret patterns fail no_secrets_detected
- F8: Append safety (10 consecutive appends)
- F9: Missing plan fails plan_exists check
- F10: Missing ledger fails ledger_exists check
- F11: Planned/tested count mismatch fails reconcile
- F12: read_latest_pointer returns None when absent
- Edge cases: run_id format, data manifest, code identity,
  transition rules, pointer loading, auto-config-key, manifest persistence

## E. Integrity check matrix

| Check | Description | Tested by |
|-------|-------------|-----------|
| manifest_exists | manifest.json present | T8, T9, T12 |
| plan_exists | research_plan.json present | T9, F9 |
| ledger_exists | candidates.jsonl present | T8, F10 |
| planned_tested_reconcile | tested+failed==planned | F11 |
| every_config_has_terminal_record | all planned keys in ledger | F11 |
| eligible_subset_of_ledger | eligible ⊂ seen keys | T8 |
| report_run_id_matches_manifest | run_id in report.md | T8 |
| data_fingerprints_exist | dataset_info has valid hashes | T8, F7 |
| code_identity_exists | git revision != "unknown" | T8 |
| cost_assumptions_exist | cost_assumptions non-empty | T8 |
| run_status_valid | status in {RUNNING,COMPLETED,PARTIAL} | T12 |
| no_secrets_detected | no api_key/token patterns | F7 |

## F. Broker safety

No broker order, position mutation, or live trading involvement. All tests
use `tmp_path` fixtures with synthetic data. The run_contract module has
zero broker dependencies — it is pure file I/O and JSONL management.

## G. Recommendation

The Canonical Research Run Contract is ready for adoption by the
strategy_architect_autopilot pipeline. Next bounded task: wire the autopilot
to use `ResearchRun.create()` → `persist_plan()` → `append_candidate()` →
`finalize_eligible()` → `complete()` → `update_latest_pointer()` in its
main loop, replacing the current ad-hoc artifact writing.

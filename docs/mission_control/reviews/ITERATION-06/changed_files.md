# Changed Files — Iteration 06

**Date:** 2026-08-29 20:10 UTC

## New Files

| File | Purpose |
|------|---------|
| `core/seeder_handoff.py` | Canonical handoff module: resolve_latest_completed_run(), validate_handoff(), seed_from_eligible(), legacy_fallback(), run_seeder_handoff() |
| `tests/test_seeder_handoff.py` | 45 tests: T1–T16 mandatory + F1–F18 failure matrix + edge cases |
| `docs/mission_control/decisions/ADR-2026-08-29-canonical-seeder-handoff.md` | ADR for canonical seeder handoff |

## Modified Files

| File | Changes |
|------|---------|
| `core/seeder.py` | Added --use-legacy/--legacy-scan/--dry-run CLI flags. Default path now uses canonical handoff. Legacy path requires explicit opt-in. Added provenance tracking (source_run_id, source_config_key). |

## Evidence Bundle (this directory)

| File | Purpose |
|------|---------|
| `prechange_seeder_sources.md` | Source inventory before change |
| `canonical_handoff_contract.md` | Handoff validation contract |
| `failure_matrix.md` | F1–F18 results matrix |
| `changed_files.md` | This file |
| `tests.md` | Test summary |
| `runtime_verification.md` | Runtime proof |
| `final_report.md` | Executive report |

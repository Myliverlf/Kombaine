# Changed Files — Iteration 07

**Date:** 2026-08-30

## New Files

| File | Purpose |
|---|---|
| `core/experiment_memory.py` (1039 lines) | Core module: two-level identity, 7-category classification, SQLite memory store, index/backfill/query API |
| `tests/test_experiment_memory.py` (58 tests) | Full test suite: T1–T17 mandatory + F1–F7 failure + edge cases |
| `docs/mission_control/07_KNOWLEDGE_AND_EXPERIMENT_MEMORY.md` | Mission Control spec for experiment memory subsystem |
| `docs/mission_control/decisions/ADR-2026-08-30-experiment-identity-and-memory.md` | ADR for two-level identity and observation-only classification |
| `docs/mission_control/reviews/ITERATION-07/` (this directory) | Evidence bundle for Iteration 07 |

## No Modified Files

Iteration 07 was purely additive. No existing files were modified.

## Evidence Bundle (this directory)

| File | Purpose |
|---|---|
| `prechange_research_memory_state.md` | Pre-change state: no experiment memory existed |
| `identity_contract.md` | Two-level identity contract |
| `memory_schema.md` | SQLite schema and constraints |
| `classification_rules.md` | 7-category classification rules |
| `failure_matrix.md` | F1–F7 + T5–T10 boundary results |
| `changed_files.md` | This file |
| `tests.md` | Test summary |
| `runtime_verification.md` | Runtime proof |
| `final_report.md` | Executive report |

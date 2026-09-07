# Memory Schema — Iteration 07

**Date:** 2026-08-30
**Store:** `state/experiment_memory.db` (SQLite, separate from analytics.db)

## Tables

| Table | Purpose |
|---|---|
| `experiment_families` | Normalized hypothesis identity (family_id, instrument, timeframe, strategy, parameters, horizon_days, methodology_version) |
| `experiment_instances` | Exact execution evidence with classification (instance_id, family_id, run_id, config_key, classification, evidence fields) |
| `indexed_runs` | Provenance record of which runs have been backfilled (run_id, indexed_at, source_type) |

## Schema Version

Tracked via `_schema_version` table. Current: `1.0.0`.

## Constraints

- DB auto-created on first use (T25: test_auto_creates_db)
- Separate from analytics.db (T26: test_memory_db_separate_from_analytics)
- Read-only DB rejected with error (T31: test_read_only_db_rejected)
- Idempotent: re-indexing same run produces no duplicate rows (T11, T22)

# Identity Contract — Iteration 07

**Date:** 2026-08-30

## Two-Level Identity

```text
experiment_family_id  = SHA-256(instrument + timeframe + strategy + normalized_params + horizon_days + methodology_version)
experiment_instance_id = SHA-256(family fields + dataset_hash + dataset_start/end + code_hash + cost_model_hash + validation_version + backtest_engine_version)
```

## Normalization Rules

| Field | Rule |
|---|---|
| instrument | uppercase, stripped |
| timeframe | lowercase, stripped |
| strategy | lowercase, hyphens/spaces → underscores |
| parameters | sorted JSON keys, whole-number floats normalized |

## Hash Contract

All hashes: SHA-256 truncated to 16 hex chars via canonical JSON serialization.
Deterministic: same inputs always produce same output (proven by T1, T2, T3).

## Invariants

- Family identity = "what question?" (stable across data windows, code versions)
- Instance identity = "what exact conditions?" (unique per execution)
- No partial or fuzzy matching; exact deterministic hashes only
- Schema version tracked in DB (MEMORY_SCHEMA_VERSION = "1.0.0")

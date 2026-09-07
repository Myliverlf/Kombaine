# ADR-2026-08-30 — Experiment Identity and Memory

**Status:** ACCEPTED
**Date:** 2026-08-30
**Iteration:** 07 — Phase 3
**Change class:** CLASS 1 — Research intelligence / metadata infrastructure

---

## Context

Iterations 05–06 established a canonical research pipeline with immutable run bundles, a candidates ledger, and a validated seeder handoff. The system can prove what happened inside a single run, but has no cross-run memory. When a new research cycle starts, there is no canonical way to answer:

> Have we already tested this experiment?
> When? On what exact data/code/cost assumptions?
> Is today's experiment identical, a legitimate revalidation, or materially different?

This gap causes redundant experimentation, undetected regressions, and invisible methodology drift.

## Decision

Implement a two-level Experiment Identity (family + instance) and a durable SQLite-backed Experiment Memory index.

### Two-Level Identity

**experiment_family_id** — Deterministic hash of the normalized hypothesis: instrument, timeframe, strategy, normalized parameters, horizon_days, methodology_version. Represents "what question are we testing?"

**experiment_instance_id** — Deterministic hash of exact execution evidence: family fields + dataset_hash, dataset_start/end, code_hash, cost_model_hash, validation_version, backtest_engine_version. Represents "what exact conditions produced this result?"

### Canonical Normalization

- Parameters: sorted JSON keys, whole-number floats normalized
- Instrument: uppercase, stripped
- Timeframe: lowercase, stripped
- Strategy: lowercase, hyphens/spaces → underscores
- All hashing: SHA-256 truncated to 16 hex chars via canonical JSON

### Memory Store

Separate SQLite database at `state/experiment_memory.db`. Not tables inside `analytics.db`. Source of truth is the immutable run bundle; memory is an index/knowledge layer.

### Classification

Seven deterministic categories: NEW, EXACT_DUPLICATE, REVALIDATION, METHODOLOGY_CHANGE, CODE_CHANGE, COST_MODEL_CHANGE, INCOMPARABLE.

### Observation Only

Iteration 07 is OBSERVATION + INDEX + CLASSIFICATION only. No VETO/SKIP/PRIORITIZE/EXECUTE decisions. No experiment may be skipped solely because memory says EXACT_DUPLICATE. The future Novelty Gate is a separate iteration.

## Consequences

- Research planners can ask "have we tested this before?" and get evidence-backed answers
- Backfill of Iteration 05+ canonical runs is safe and idempotent
- Legacy pre-canonical runs are recorded as LEGACY_UNINDEXED, never fabricated
- Every memory instance links back to run_id + config_key provenance
- No broker, no live, no strategy/risk changes
- Classification cannot alter experiment execution

## Evidence

- `core/experiment_memory.py` — 1000+ lines, SQLite schema, identity generation, classification, indexing, query API
- `tests/test_experiment_memory.py` — 58 tests covering T1–T17 + F1–F16
- Runtime verification: backfill report, idempotency proof, classification examples

# Mission Control — 07 Knowledge and Experiment Memory

**Status:** IMPLEMENTED · 2026-08-30
**Iteration:** 07 — Phase 3

## Purpose

Durable cross-run experiment memory: the system can now answer "have we tested this before?" with evidence-backed classification.

## Architecture

### Two-Level Identity

```text
experiment_family_id
  = SHA-256(normalized hypothesis)
  = instrument + timeframe + strategy + normalized_params + horizon + methodology

experiment_instance_id
  = SHA-256(exact evidence)
  = family fields + dataset_hash + code_hash + cost_model_hash + validation + engine
```

Family = "what question?" (stable across data windows, code versions)
Instance = "what exact conditions?" (unique per execution)

### Memory Store

```text
state/experiment_memory.db  (SQLite, separate from analytics.db)
```

Tables:
- `experiment_families` — normalized hypothesis identity
- `experiment_instances` — exact execution evidence with classification
- `indexed_runs` — provenance record of which runs have been backfilled

### Classification

| Category | Meaning |
|---|---|
| NEW | No prior compatible family exists |
| EXACT_DUPLICATE | Same exact instance identity already exists |
| REVALIDATION | Same family, legitimately newer data window |
| METHODOLOGY_CHANGE | Validation/backtest methodology differs |
| CODE_CHANGE | Strategy implementation identity differs |
| COST_MODEL_CHANGE | Commission/slippage/sizing assumptions differ |
| INCOMPARABLE | Insufficient evidence to compare |

### Query API

- `find_history(family_id)` — all instances of a family, chronological
- `find_exact_instance(instance_id)` — one specific instance
- `find_by_run(run_id)` — all instances from one run
- `find_by_config_key(config_key)` — all instances with same config
- `classify_candidate(...)` — classify against full history
- `recent_history(strategy, instrument, timeframe)` — recent instances

### Observation Only

Classification is INFORMATIONAL. It does NOT:
- Skip experiments
- VETO candidates
- Change priorities
- Alter execution flow

The future Novelty Gate will consume classification results.

## Integration Points

- Indexing: `ExperimentMemory.index_run(run_bundle_path)` after COMPLETED run
- Backfill: `ExperimentMemory.backfill(runs_dir)` for historical runs
- Pre-run: `ExperimentMemory.classify_candidate(...)` for observation
- Reporting: `ExperimentMemory.summary()` for derived views

## Files

| File | Role |
|---|---|
| `core/experiment_memory.py` | Core module: identity, classification, SQLite store |
| `tests/test_experiment_memory.py` | 58 tests (T1–T17 + F1–F16) |
| `state/experiment_memory.db` | SQLite memory store |

## Constraints

- CLASS 1: research intelligence only
- No broker orders, no live, no strategy/risk changes
- Legacy runs: LEGACY_UNINDEXED, never fabricated
- Backfill: only Iteration 05+ canonical runs with COMPLETED status
- Idempotent: re-indexing a run produces no duplicate rows

---

# Research Knowledge Layer — Iteration 09

## Purpose

Convert groups of experiment observations into evidence-backed findings, confidence, contradictions, and unresolved questions. Read-only: cannot mutate registry, novelty policy, or trading.

## Architecture

### Finding Types

PERFORMANCE, ROBUSTNESS, FAILURE_PATTERN, COST_SENSITIVITY, DATA_SENSITIVITY, REVALIDATION_TREND, CONTRADICTION, INSUFFICIENT_EVIDENCE

### Finding Statuses

ACTIVE, CONTESTED, WEAKENED, SUPERSEDED, RETRACTED, INSUFFICIENT

### Confidence Levels

INSUFFICIENT (< 2 observations), LOW (2-3), MEDIUM (4+), HIGH (6+ with revalidations)

### Knowledge Store

```text
state/research_knowledge.db  (SQLite, separate from experiment_memory.db)
```

Tables:
- `research_findings` — canonical evidence-backed statements
- `finding_history` — material finding changes preserved
- `knowledge_builds` — build provenance records
- `open_questions` — unresolved knowledge gaps

### Query API

- `find_findings(subject, type, status, instrument)` — find findings with filters
- `find_strategy_knowledge(strategy_family)` — all knowledge about a strategy
- `find_contested()` — contested/contradicted findings
- `find_insufficient()` — insufficient evidence findings
- `explain_finding(finding_id)` — full provenance + evidence chain + history
- `get_evidence(finding_id)` — evidence chain for a finding
- `get_open_questions(status, reason)` — unresolved knowledge gaps
- `summary()` — derived summary report

### Evidence Contract

Every finding → evidence_refs → experiment_instance_id + run_id → Experiment Memory → run bundle.

### Comparability Gate

Union-find grouping on: strategy, instrument, timeframe, validation_version, cost_model_hash, parameters. Incompatible observations separated, not averaged.

### Contradiction Handling

First-class: CONTRADICTION type, CONTESTED status, reduced confidence, both supporting and contradicting evidence tracked, open questions generated.

### Survivorship Bias Protection

Distiller queries ALL experiment_instances (not just eligible). Rejected/negative evidence included.

## Hard Boundaries

- Knowledge ≠ Policy ≠ Promotion ≠ Trading Signal
- Knowledge cannot mutate registry, novelty policy, or trading
- Findings are read-only observations

## Files

| File | Role |
|---|---|
| `core/research_knowledge.py` | Core module: types, store, distiller, queries |
| `tests/test_research_knowledge.py` | 106 tests (T1–T21 + F1–F18) |
| `state/research_knowledge.db` | SQLite knowledge store |

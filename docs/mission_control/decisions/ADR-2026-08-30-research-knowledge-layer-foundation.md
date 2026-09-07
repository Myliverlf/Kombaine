# ADR-2026-08-30: Research Knowledge Layer Foundation

**Status:** ACCEPTED  
**Date:** 2026-08-30  
**Iteration:** 09  
**Change class:** CLASS 1 — Research intelligence / knowledge infrastructure

## Context

Iterations 01–08 built: canonical runs, canonical handoff, Experiment Identity & Memory, Novelty Gate, and exact duplicate suppression. The system can now answer "have we tested this experiment before?" with evidence-backed classification.

It cannot yet answer: "What have we learned from all related experiments?"

## Decision

Build the first canonical Research Knowledge Layer that converts groups of experiment observations into evidence-backed findings, confidence, contradictions, and unresolved questions — without granting those findings authority over trading, selection, risk, execution, or autonomous research generation.

## Architecture

### Knowledge Unit — ResearchFinding

A `ResearchFinding` is an evidence-backed statement synthesized from one or more observations. Every finding has:
- Deterministic identity (finding_id) based on type + subject + scope
- Scope (what it applies to: instruments, strategies, timeframes)
- Evidence chain (observation refs → instances → families → run_id/config_key)
- Confidence with explicit basis
- Status (lifecycle)
- Build provenance

### Finding Types

| Type | Meaning |
|---|---|
| PERFORMANCE | Directional performance across observations |
| ROBUSTNESS | Consistent results across instruments/conditions |
| FAILURE_PATTERN | Consistent failure across observations |
| COST_SENSITIVITY | Results differ across cost models |
| DATA_SENSITIVITY | Results differ across data windows |
| REVALIDATION_TREND | Revalidation results differ from original |
| CONTRADICTION | Evidence points in opposing directions |
| INSUFFICIENT_EVIDENCE | Cannot draw conclusion |

### Finding Statuses

| Status | Meaning |
|---|---|
| ACTIVE | Evidence supports the finding |
| CONTESTED | Contradicting evidence exists |
| WEAKENED | Confidence reduced by new evidence |
| SUPERSEDED | Replaced by newer finding |
| RETRACTED | Evidence withdrawn or invalidated |
| INSUFFICIENT | Cannot determine direction |

### Confidence Model

| Level | Requirements |
|---|---|
| INSUFFICIENT | < 2 comparable observations |
| LOW | 2-3 observations, low consistency |
| MEDIUM | 4+ observations, moderate consistency, some trades |
| HIGH | 6+ observations, high consistency, many trades, multiple revalidations |

Contradictions reduce confidence. Every confidence level has explicit basis text.

### Evidence Contract

Every finding resolves to:
```
finding → observation(s) → instance/family → run_id/config_key → bundle
```

A finding without resolvable evidence is invalid.

### Comparability Gate

Before aggregating observations, verify they are comparable on:
- strategy, instrument, timeframe, validation_version, cost_model_hash, parameters

Incompatible observations are separated into different findings, not averaged.

### Contradiction Handling

Contradictions are first-class:
- Supporting and contradicting observations are both tracked
- Finding status becomes CONTESTED
- Confidence is reduced
- Open questions are generated

### Survivorship Bias Protection

Distillation queries ALL experiment_instances, not just eligible candidates. Rejected/negative evidence is included in findings.

### Duplicate Reproduction Weighting

Exact duplicate observations (same instance_id) are grouped and counted once for evidence strength. They verify reproducibility but do not inflate independent evidence.

### Missing Metrics

Missing metrics remain missing (not zero). `metrics_missing` tracks which metrics are absent.

### Build Provenance

Each distillation execution has:
- knowledge_build_id (deterministic from timestamp)
- distiller_version
- source_memory_version
- finding counts (created/updated/unchanged)
- errors
- status

### Knowledge Store

Separate SQLite: `state/research_knowledge.db`

Tables:
- `research_findings` — canonical evidence-backed statements
- `finding_history` — material finding changes preserved
- `knowledge_builds` — build provenance records
- `open_questions` — unresolved knowledge gaps

### Open Questions

Structured unresolved knowledge gaps:
- INSUFFICIENT_REVALIDATION
- CONTRADICTORY_RESULTS
- MISSING_COST_SENSITIVITY
- STALE_EVIDENCE

`priority_hint` is descriptive only, not autonomous scheduling authority.

## Hard Boundaries

```
KNOWLEDGE ≠ POLICY
KNOWLEDGE ≠ PROMOTION
KNOWLEDGE ≠ TRADING SIGNAL
KNOWLEDGE ≠ AUTONOMOUS HYPOTHESIS GENERATION
```

Research Knowledge Store:
- CANNOT promote/demote strategies
- CANNOT alter watchlist/signal pool
- CANNOT trigger swaps
- CANNOT change risk limits
- CANNOT modify novelty policy
- CANNOT generate new research plans

## Consequences

### Positive
- System can answer "what have we learned?" with evidence
- Contradictions are visible, not hidden
- Confidence is explicit and auditable
- Negative evidence is preserved
- Build provenance enables reproducibility

### Negative
- Additional SQLite store to maintain
- Distiller must be re-run when new observations arrive
- Confidence thresholds may need tuning as evidence accumulates

## Files Changed

| File | Change |
|---|---|
| `core/research_knowledge.py` | New module: FindingType, FindingStatus, ConfidenceLevel, ResearchFinding, KnowledgeStore, distill_findings, comparability gate, confidence model, query API, open questions |
| `tests/test_research_knowledge.py` | 106 tests (T1–T21 + F1–F18) |
| `state/research_knowledge.db` | New SQLite knowledge store |

## Evidence

- 106 tests passing (T1–T21 + F1–F18)
- No broker orders created
- No registry/novelty/trading mutations

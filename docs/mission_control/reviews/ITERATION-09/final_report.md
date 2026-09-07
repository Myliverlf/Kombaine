# Final Report — Iteration 09: Research Knowledge Layer Foundation

## A. Executive Result

Research Knowledge Layer: **YES**

## B. Previous Knowledge Gap

Experiment Memory could remember "have we tested this before?" but not "what have we learned from all related experiments?"

## C. Knowledge Model

```
DATA → EXPERIMENT → OBSERVATION → FINDING → DECISION (boundary)
                                        ↑
                               Iteration 09 creates this
```

Finding ≠ Decision. Findings are read-only observations. They cannot mutate registry, novelty policy, or trading.

## D. Canonical Store

- Path: `state/research_knowledge.db` (separate SQLite)
- Schema: 4 tables (research_findings, finding_history, knowledge_builds, open_questions)
- Source of truth: immutable run bundles, NOT the knowledge store itself

## E. Finding Taxonomy

8 finding types: PERFORMANCE, ROBUSTNESS, FAILURE_PATTERN, COST_SENSITIVITY, DATA_SENSITIVITY, REVALIDATION_TREND, CONTRADICTION, INSUFFICIENT_EVIDENCE

6 finding statuses: ACTIVE, CONTESTED, WEAKENED, SUPERSEDED, RETRACTED, INSUFFICIENT

4 confidence levels: INSUFFICIENT, LOW, MEDIUM, HIGH

## F. Evidence Contract

Every finding → evidence_refs → experiment_instance_id + run_id → Experiment Memory → run bundle. No finding without resolvable evidence.

## G. Comparability

Union-find grouping on: strategy, instrument, timeframe, validation_version, cost_model_hash, parameters. Incompatible observations separated, not averaged.

## H. Confidence

Explicit model based on evidence count, revalidations, consistency, trade count, contradiction count. Every level has basis text. No arbitrary percentages.

## I. Contradictions

First-class: CONTRADICTION type, CONTESTED status, reduced confidence, both supporting and contradicting evidence tracked, open questions generated.

## J. Survivorship Bias Protection

Distiller queries ALL experiment_instances (not just eligible). Rejected/negative evidence included in findings.

## K. Reproduction Weighting

Exact duplicates (same instance_id) counted once for evidence strength. Verify reproducibility but don't inflate independent evidence.

## L. Knowledge Builds

Deterministic build_id from timestamp. Records: distiller_version, source_memory_version, finding counts, errors, status. Idempotent rebuild.

## M. Query Interface

- find_findings(subject, type, status, instrument)
- find_strategy_knowledge(strategy_family)
- find_contested()
- find_insufficient()
- explain_finding(finding_id) — returns evidence chain + history + build provenance
- get_evidence(finding_id)
- get_open_questions(status, reason)
- summary()

## N. Files Changed

- `core/research_knowledge.py` — new module (~900 lines)
- `tests/test_research_knowledge.py` — 106 tests (~1500 lines)
- `docs/mission_control/decisions/ADR-2026-08-30-research-knowledge-layer-foundation.md`
- `docs/mission_control/reviews/ITERATION-09/` — 11 evidence files
- Updated: 01_SYSTEM_MAP, 02_SOURCE_OF_TRUTH, 07_KNOWLEDGE_AND_EXPERIMENT_MEMORY, 08_TECH_DEBT_REGISTER, 09_ROADMAP, 10_MATURITY_MODEL

## O. Tests

106 tests: T1–T21 + F1–F18 + additional. ALL PASSING.

## P. Runtime Verification

Experiment Memory has 0 observations. All findings are fixture/test-based. Real strategy knowledge will populate when canonical runs are indexed. This is expected.

## Q. Safety Confirmation

```
real broker orders created for Iteration 09: NO
broker positions intentionally changed: NO
strategy registry mutated by knowledge: NO
novelty skip policy changed by knowledge: NO
research plans autonomously generated: NO
strategy promotion/demotion caused by findings: NO
mode changed: NO
paper_first changed: NO
strategy semantics changed: NO
risk limits changed: NO
```

## R. Mission Control Updates

- ADR: ADR-2026-08-30-research-knowledge-layer-foundation.md
- Tech debt: TD-020 added
- Roadmap: P2 item completed
- Maturity: KNOWLEDGE MEMORY score updated

## S. Remaining Phase 5 Gaps

- Real canonical run needed to populate knowledge with actual evidence
- Decay/lifecycle policy for stale findings (Iteration 10+)
- Regime detection integration (requires reliable regime labels)
- Parameter-neighbor intelligence (Iteration 10+)

## T. Recommended Next Task

**Iteration 10: Knowledge Decay & Lifecycle Policy**
Define staleness thresholds and lifecycle transitions for findings. When findings age without new evidence, make staleness visible through the query layer. No autonomous scheduling.

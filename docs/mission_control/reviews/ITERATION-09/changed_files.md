# Changed Files — Iteration 09

## New Files

| File | Lines | Purpose |
|---|---|---|
| core/research_knowledge.py | ~900 | Research Knowledge Layer: FindingType, FindingStatus, ConfidenceLevel, ResearchFinding, KnowledgeStore, distill_findings, comparability gate, confidence model, query API, open questions |
| tests/test_research_knowledge.py | ~1500 | 106 tests (T1–T21 + F1–F18) |
| docs/mission_control/decisions/ADR-2026-08-30-research-knowledge-layer-foundation.md | ~150 | Architecture Decision Record |
| docs/mission_control/reviews/ITERATION-09/ | 11 files | Evidence bundle |
| state/research_knowledge.db | auto | SQLite knowledge store (created on first use) |

## Modified Files

| File | Change |
|---|---|
| docs/mission_control/01_SYSTEM_MAP.md | Added Research Knowledge Layer |
| docs/mission_control/02_SOURCE_OF_TRUTH.md | Added knowledge conclusion authority |
| docs/mission_control/07_KNOWLEDGE_AND_EXPERIMENT_MEMORY.md | Added Iteration 09 section |
| docs/mission_control/08_TECH_DEBT_REGISTER.md | Added TD-020 |
| docs/mission_control/09_ROADMAP.md | Updated P2 items |
| docs/mission_control/10_MATURITY_MODEL.md | Updated KNOWLEDGE MEMORY score |

## Unchanged Files

| File | Reason |
|---|---|
| core/experiment_memory.py | Read-only source for distillation |
| core/novelty_gate.py | Not modified by knowledge layer |
| code/strategy_registry.py | Knowledge cannot mutate registry |

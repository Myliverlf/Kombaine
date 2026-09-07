# Source Inventory — Iteration 16

**Date:** 2026-08-30

## Read Sources
| Source | Module | Purpose |
|---|---|---|
| Strategy Registry | code/strategy_registry.py | Canonical strategy state |
| Performance Attribution | core/performance_attribution.py | Operational evidence |
| Strategy Lifecycle | core/strategy_lifecycle.py | Decay/health status |
| Market Regime | core/market_regime.py | Regime classification |
| Research Knowledge | core/research_knowledge.py | Research findings |

## Write Targets
| Target | Type | Purpose |
|---|---|---|
| state/replacement_ranking.db | SQLite (derived) | Ranking store |
| reports/replacement_ranking/ | JSON + MD | Human-readable reports |

## Excluded Sources
- Broker API (read-only for positions, never called by ranking)
- Signal pool / waitlist (derived from registry, not read separately)

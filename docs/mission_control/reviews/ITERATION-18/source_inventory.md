# Source Inventory — Iteration 18

## Canonical Sources Read by Mission Control

| Source | Module | DB/File | Purpose |
|--------|--------|---------|---------|
| System Health | core/system_health.py | reports/ | Component health, invariants, schedulers |
| Research Pipeline | core/research_pipeline.py | reports/strategy_architect/ | Research status, latest run |
| Strategy Lifecycle | core/strategy_lifecycle.py | state/strategy_lifecycle.db | Decay detection, revalidation status |
| Performance Attribution | core/performance_attribution.py | state/performance_attribution.db | Attribution health, conflicts |
| Market Regime | core/market_regime.py | state/regime_snapshot.json | Regime health |
| Replacement Ranking | core/replacement_ranking.py | state/replacement_ranking.db | Replacement candidates |
| Human Review | core/human_review.py | state/human_review.db | Open review cases |
| Experiment Memory | core/experiment_memory.py | state/experiment_memory.db | Novelty consultation |
| Research Knowledge | core/research_knowledge.py | state/research_knowledge.db | Knowledge status |

## Mission Control State Store

| Table | Purpose |
|-------|---------|
| mc_cycles | Cycle lifecycle tracking |
| mc_snapshots | Immutable system snapshots |
| mc_decisions | Decision records with explainability |
| mc_tasks | Non-trading task lifecycle |
| mc_task_events | Task state transition journal |
| mc_incidents | Incident lifecycle |
| mc_revalidation_requests | Revalidation requests |
| mc_alert_events | Alert events with rate limiting |
| mc_policy_versions | Policy version history |

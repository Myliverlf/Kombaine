# Source of Truth Matrix — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED

| Domain | Canonical Source | Derived Views | Writers | Readers | Status |
|--------|-----------------|---------------|---------|---------|--------|
| Broker positions/fills/money | Tinkoff broker API | state/portfolio.json, analytics.db, reports/ | Tinkoff API (external), broker_evidence (read-only) | supervisor, engine, analytics, broker_evidence | VERIFIED |
| Execution intents | analytics.db execution_intents | state/intent exports | execution_journal, engine | execution_journal, analytics, broker_evidence | VERIFIED |
| Analytics trades | analytics.db | state/analytics.db (empty), dashboard artifacts | analytics, execution_journal | analytics, reporting | VERIFIED |
| Strategy lifecycle registry | state/strategy_registry.json | state/waitlist.json, state/signal_pool.json | seeder_registry | supervisor, signal_pool, control_plane, risk_manager | VERIFIED |
| Canonical research runs | reports/strategy_architect/runs/{run_id}/manifest.json | reports/strategy_architect/latest_run.json | run_contract | experiment_memory, seeder_handoff, research_knowledge | VERIFIED |
| Experiment Memory | state/experiment_memory.db | state/experiment_memory exports | experiment_memory | research_knowledge, strategy_lifecycle, novelty_gate | VERIFIED |
| Research Knowledge | state/research_knowledge.db | state/research_knowledge exports | research_knowledge | strategy_lifecycle, query API | VERIFIED |
| Lifecycle observations | state/strategy_lifecycle.db | state/lifecycle exports | strategy_lifecycle | seeder_registry, lifecycle_metrics | VERIFIED |
| Signal pool / waitlist | DERIVED from strategy_registry.json | state/signal_pool.json, state/waitlist.json | signal_pool_exporter | supervisor, risk_manager | DERIVED |
| Latest pointers | DERIVED — latest_run.json | — | run_contract | system_health, seeder_handoff | DERIVED |
| Runtime configuration | config.json (runtime-parsed) | docs config documentation | manual edit | control_plane, engine, supervisor, risk_manager | VERIFIED |
| Portfolio operational state | state/portfolio.json | portfolio.before_*.json backups | supervisor, engine | supervisor, risk_manager, analytics | VERIFIED |

## Resolution rules

When sources disagree:
1. Broker API wins for positions/fills/money
2. strategy_registry.json wins for candidate lifecycle
3. Specific run bundle wins for research results
4. Runtime-parsed config.json wins for configuration
5. Derived views (signal_pool, waitlist) NEVER override canonical sources

# Dependency Graph — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED

## Critical dependency path

```text
DATA → RESEARCH → EXPERIMENT_MEMORY → NOVELTY_GATE
                                   ↘ RESEARCH_KNOWLEDGE → STRATEGY_LIFECYCLE
                                                         ↘ SEEDER_HANDOFF
                                                           ↘ SEEDER_REGISTRY
                                                             ↘ SIGNAL_POOL
                                                               ↘ RISK_MANAGER
                                                                 ↘ SUPERVISOR → ENGINE
                                                                   ↘ EXECUTION_JOURNAL → ANALYTICS
                                                                    ↘ BROKER_EVIDENCE
```

## Machine-readable edges (25 total)

| From | To | Edge Type |
|------|----|-----------|
| data_downloader | run_contract | scheduled_by |
| run_contract | experiment_memory | calls |
| experiment_memory | novelty_gate | calls |
| experiment_memory | research_knowledge | calls |
| novelty_gate | research_knowledge | reads |
| research_knowledge | strategy_lifecycle | calls |
| experiment_memory | strategy_lifecycle | reads |
| strategy_lifecycle | seeder_handoff | calls |
| run_contract | seeder_handoff | reads |
| seeder_handoff | seeder_registry | calls |
| seeder_registry | strategy_registry_json | writes |
| strategy_registry_json | signal_pool | reads |
| strategy_registry_json | waitlist | reads |
| signal_pool | supervisor | reads |
| signal_pool | risk_manager | reads |
| config_json | risk_manager | reads |
| risk_manager | supervisor | calls |
| portfolio_json | supervisor | reads |
| portfolio_json | engine | reads |
| supervisor | engine | calls |
| engine | execution_journal | calls |
| execution_journal | broker_evidence | reads |
| execution_journal | analytics | calls |
| broker_evidence | analytics | reads |
| supervisor | analytics | reads |

## Cycles

**No cycles detected** in the critical dependency path.

## Forbidden edges (tested)

| Module | Forbidden From | Reason |
|--------|---------------|--------|
| research_knowledge | broker, tinkoff, execution_journal, engine | Knowledge must not affect execution |
| strategy_lifecycle | broker, tinkoff, engine, seeder | Lifecycle must not bypass research |
| experiment_memory | broker, tinkoff, engine, strategy_registry | Memory must not mutate selection |
| novelty_gate | broker, tinkoff, engine, strategy_registry | Novelty must not bypass selection |
| seeder_handoff | tinkoff, broker, engine | Handoff must not execute |

## Multiple-writer stores

| Store | Writers | Severity |
|-------|---------|----------|
| state/portfolio.json | supervisor, engine | P2 (operational) |
| analytics.db | execution_journal, analytics | P2 (reporting) |

## Hidden cross-layer imports

- `core/supervisor.py` imports from `tinkoff.invest` (justified: broker interface via engine)
- `core/engine.py` imports `tinkoff.invest` (justified: broker interface)
- `core/seeder.py` imports `tinkoff.invest` and `futures_lab` (justified: data/backtest)

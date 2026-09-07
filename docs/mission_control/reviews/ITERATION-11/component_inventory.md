# Component Inventory — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED via code inspection

## DATA Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| data_downloader | 15m OHLCV download pipeline | combine-15m.timer | Tinkoff API | CSVs in futures_lab/artifacts/ | NO | systemd timer |

## RESEARCH Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| research_run_contract | Canonical research run definition + immutable bundle | core/run_contract.py | config.json, OHLCV CSVs | reports/strategy_architect/runs/{run_id}/ | NO | manual / autopilot |
| experiment_memory | Two-level identity + 7-category classification + SQLite index | core/experiment_memory.py | run bundle, experiment_memory.db | state/experiment_memory.db | NO | manual |
| novelty_gate | Duplicate/near-duplicate detection | core/novelty_gate.py | novelty_policy.json, experiment_memory.db | novelty decisions (log only) | NO | manual |

## KNOWLEDGE Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| research_knowledge | Evidence aggregation: 8 finding types, 4 confidence levels | core/research_knowledge.py | experiment_memory.db, research_knowledge.db | state/research_knowledge.db | NO | manual |

## SELECTION Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| strategy_lifecycle | Strategy lifecycle stages tracking | core/strategy_lifecycle.py | research_knowledge.db, experiment_memory.db | state/strategy_lifecycle.db | NO | manual |
| seeder_handoff | Validate + handoff eligible candidates | core/seeder_handoff.py | eligible_candidates.json | handoff manifest | NO | manual |
| seeder_registry | Canonical strategy registry (source of truth) | core/strategy_registry.py, code/strategy_registry.py | eligible_candidates.json | state/strategy_registry.json | NO | combine-seeder.timer |
| signal_pool | Derived view of active signals | code/signal_pool_exporter.py | state/strategy_registry.json | state/signal_pool.json (DERIVED) | NO | combine-seeder.timer |
| supervisor | Main operational loop | core/supervisor.py | signal_pool.json, portfolio.json, config.json | portfolio.json, execution_intents | INDIRECT | combine-supervisor.timer |

## RISK Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| risk_manager | Risk limits, position sizing, universe admission | core/risk.py | config.json, portfolio.json | (none — read-only decisions) | NO | supervisor |

## EXECUTION Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| execution_journal | Record execution intents, track status | core/execution_journal.py | analytics.db | analytics.db (execution_intents) | NO | supervisor/engine |
| engine | Tinkoff broker interface with paper/live VETO | core/engine.py | portfolio.json, config.json | portfolio.json | YES (VETO) | supervisor |

## BROKER Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| broker_evidence | Read-only broker evidence resolver | core/broker_evidence.py | Tinkoff API (read-only) | reconciliation evidence | READ-ONLY | manual |

## ANALYTICS Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| analytics | Trade/slot-event analytics, reconciliation | core/analytics.py | analytics.db | analytics.db, reports/ | NO | manual |

## CONTROL_PLANE Domain

| Component | Purpose | Entrypoints | Stores Read | Stores Written | Broker | Scheduler |
|-----------|---------|-------------|-------------|----------------|--------|-----------|
| control_plane | Config management, mode/paper_first enforcement | core/config.py | config.json | (none — read-only) | NO | startup |

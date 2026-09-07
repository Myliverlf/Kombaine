# Recurring Factory Runtime — Iteration 22

## Canonical Research Cycle
- **Owner:** PipelineCoordinator (core/research_pipeline.py)
- **Scheduler:** combine-research-daily.timer (systemd)
- **Budget:** 250 candidates/day
- **Universe:** [BR, GAZP, LKOH, SBER, Si]

## Pipeline Stages
PRECHECK → DATA_READY → PLAN_CREATED → RESEARCH → RUN_INTEGRITY →
MEMORY_INDEX → NOVELTY_VERIFY → ELIGIBILITY → SEEDER_HANDOFF →
KNOWLEDGE_BUILD → LIFECYCLE_BUILD → HEALTH_VERIFY → REPORT → COMPLETE

## Real Cycle Report
The factory is operational and produces deterministic daily plans.
Zero eligible candidates is a valid result — no manufacturing.

## Experiment Memory Delta
- Families: 340 indexed
- Instances: 460 indexed
- Each run adds new experiments to memory

## Knowledge Delta
Research knowledge persists observations about strategy performance.

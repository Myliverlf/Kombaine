# Changed Files — Iteration 13B

## New Files

| File | Purpose |
|------|---------|
| /etc/systemd/system/combine-research-daily.timer | Daily 06:00 timer |
| /etc/systemd/system/combine-research-daily.service | Oneshot service for pipeline |
| scripts/canonical_daily_research.sh | Wrapper script for systemd ExecStart |
| docs/mission_control/decisions/ADR-2026-08-30-canonical-daily-research-scheduler-activation.md | ADR |
| docs/mission_control/reviews/ITERATION-13B/ | Evidence bundle |

## Modified Files

| File | Change |
|------|--------|
| core/research_pipeline.py | Fixed premature lock release in acquire_lock() (both normal and stale-lock paths) |
| core/system_health.py | Added combine-research-daily.timer to scheduler_inventory; added scheduler health component |
| docs/mission_control/OPERATIONS_RUNBOOK.md | Added scheduler operational commands |

## Unchanged Files

| File | Reason |
|------|--------|
| config.json | mode=paper, paper_first=true preserved |
| All eligibility config | Hash unchanged |
| combine-15m.service/timer | Not touched |
| combine-seeder.service/timer | Not touched |
| combine-supervisor.service/timer | Not touched |

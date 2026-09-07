# Scheduler Inventory — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED

## Systemd Timers

| Job ID | Command | Source | Cadence | Lock | Ownership |
|--------|---------|--------|---------|------|-----------|
| combine-15m | download_15m.sh | systemd | OnCalendar | systemd_overlap | SINGLE_OWNER |
| combine-seeder | core/seeder.py | systemd | OnCalendar | systemd_overlap | SINGLE_OWNER |
| combine-supervisor | core/supervisor.py | systemd | OnCalendar | fcntl (.supervisor.lock) | SINGLE_OWNER |

## Systemd Services (on-demand)

| Job ID | Command | Source | Cadence | Lock | Ownership |
|--------|---------|--------|---------|------|-----------|
| combine-lkoil | specific instrument task | systemd | on-demand | none | SINGLE_OWNER |
| pi-combine-tasks | Pi agent tasks | systemd | on-demand | none | SINGLE_OWNER |
| prop-daemon | prop desk daemon | systemd | on-demand | none | SINGLE_OWNER |
| prop-desk-daemon | prop desk daemon | systemd | on-demand | none | SINGLE_OWNER |
| prop-trigger | trigger service | systemd | on-demand | none | SINGLE_OWNER |

## Internal Loops

| Job ID | Command | Source | Cadence | Lock | Ownership |
|--------|---------|--------|---------|------|-----------|
| supervisor_loop | core/supervisor.py (internal sleep) | internal_loop | configurable_sleep | fcntl (.supervisor.lock) | SINGLE_OWNER |

## Manual Entrypoints

| Job ID | Command | Source | Cadence | Lock | Ownership |
|--------|---------|--------|---------|------|-----------|
| code_strategy_architect_autopilot | code/strategy_architect_autopilot.py | manual | manual | research_lock | SINGLE_OWNER |
| core_seeder | core/seeder.py | manual | manual | research_lock | SINGLE_OWNER |

## Duplicate Ownership Detection

**Result:** NO DUPLICATE OWNERS DETECTED

Each recurring job has exactly one scheduling owner.
- Data download: systemd only
- Seeder: systemd only (manual entrypoint is intentional alternative)
- Supervisor: systemd only (internal loop is the actual executor)
- Research: manual only (no systemd timer for research runs)

## Dependency-aware scheduling

```text
DATA READY (combine-15m.timer)
→ RESEARCH (manual / autopilot)
→ EXPERIMENT MEMORY (manual)
→ KNOWLEDGE BUILD (manual)
→ LIFECYCLE OBSERVER (manual)
→ SEEDER / REGISTRY (combine-seeder.timer)
→ SIGNAL POOL (derived)
→ SUPERVISOR (combine-supervisor.timer)
→ EXECUTION (supervisor → engine)
```

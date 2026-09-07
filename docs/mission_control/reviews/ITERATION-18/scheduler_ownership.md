# Scheduler Ownership — Iteration 18

## Existing Timers
| Timer | Owner | Cadence | Purpose |
|-------|-------|---------|---------|
| combine-research-daily.timer | research_pipeline | 06:00 daily | Canonical research |
| combine-15m.timer | data_downloader | Every 15 min | Data download |
| combine-supervisor.timer | supervisor | Configurable | Signal/risk supervision |

## Mission Control Scheduling
- MC runs on-demand or after major pipeline events
- NO competing timer added
- NO modification to existing research timer
- Lock prevents concurrent MC cycles
- Stale lock recovery ensures single-owner guarantee

## Proven
- Existing timers inventory documented
- No duplicate owner detected
- MC does not replace canonical research owner

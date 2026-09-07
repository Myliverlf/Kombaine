# Scheduler Ownership — After Activation

**Date:** 2026-08-30 01:17 UTC

## Active Canonical Research Scheduler Owners: 1

| Owner | Unit | Schedule | Status |
|-------|------|----------|--------|
| combine-research-daily.timer | combine-research-daily.service | 06:00:00 daily | active (waiting) |

## Inactive Legacy Jobs (on disk, cannot schedule canonical work)

None found. No research-related cron jobs or legacy timers exist.

## Non-Research Timers (unchanged)

| Timer | Purpose |
|-------|---------|
| combine-15m.timer | 15m candle download |
| combine-seeder.timer | Waitlist seeder |
| combine-supervisor.timer | Portfolio supervisor |

## Verdict

ACTIVE_CANONICAL_RESEARCH_SCHEDULER_OWNERS = 1
Exactly one canonical owner: combine-research-daily.timer

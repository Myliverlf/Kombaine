# ADR-2026-08-30: Canonical Daily Research Scheduler Activation

**Status:** Accepted
**Date:** 2026-08-30
**Iteration:** 13B
**Deciders:** Mission Control (Hermes)

## Context

Iteration 13 implemented the full research pipeline coordinator with:
- Stage graph with dependency enforcement
- fcntl.flock-based global pipeline lock
- Restart/resume semantics
- Daily budget cap (250 candidates/day)
- Eligibility policy hash (SHA-256)
- Missed-run policy (max 1 catch-up/day)

The recurring daily scheduler was intentionally NOT activated without explicit authorization. Iteration 13B provides that authorization.

## Decision

Activate exactly one canonical daily research scheduler using systemd timer/service.

### Timer/Service Identity

| Object | Name |
|--------|------|
| Timer unit | combine-research-daily.timer |
| Service unit | combine-research-daily.service |
| Wrapper script | scripts/canonical_daily_research.sh |

### Schedule

| Property | Value |
|----------|-------|
| OnCalendar | *-*-* 06:00:00 |
| Timezone | CEST (system local, UTC+2) |
| Persistent | true |
| AccuracySec | 1min |

### Why systemd

- Single canonical owner (no competing cron/Hermes/supervisor research jobs)
- Persistent=true handles missed runs (system off at 06:00)
- Journal integration for failure visibility
- `systemctl status` for operational commands
- Consistent with existing combine-15m/combine-seeder/combine-supervisor timers

### Lock Behavior

- fcntl.flock on state/.research_pipeline.lock
- Non-blocking acquire (returns False if locked)
- Stale lock detection via PID liveness check
- Lock released only in release_lock() (bug fixed in this iteration)

### Immediate Proof Method

1. `systemctl start combine-research-daily.service` (manual trigger)
2. Observe pipeline_run_id in coordinator output
3. Verify manifest persisted in reports/research_pipeline/runs/<id>/
4. Verify latest.json pointer updated
5. Prove second concurrent invocation DENIED by lock

### Health Visibility

Added `scheduler` component to system_health.py:
- Timer active state
- Next trigger time
- Last pipeline run ID and status
- Pipeline lock state

### Disable/Recovery Procedure

```bash
# Disable
systemctl stop combine-research-daily.timer
systemctl disable combine-research-daily.timer

# Re-enable
systemctl daemon-reload
systemctl enable --now combine-research-daily.timer

# Manual trigger
systemctl start combine-research-daily.service

# Check status
systemctl status combine-research-daily.timer
systemctl status combine-research-daily.service
```

### Broker Isolation

- Service runs in paper mode only
- Zero broker-mutating API calls
- No position changes
- No order creation
- Execution scheduling unchanged

## Consequences

- Daily research pipeline fires at 06:00 CEST automatically
- Missed runs (system off) catch up on next boot (max 1/day)
- Lock prevents concurrent pipeline execution
- Health report includes scheduler state
- Runbook updated with exact operational commands

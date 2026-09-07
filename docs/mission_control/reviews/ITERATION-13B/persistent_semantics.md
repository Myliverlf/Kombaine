# Persistent Semantics — Iteration 13B

## Configuration

| Property | Value |
|----------|-------|
| Persistent | true |
| OnCalendar | *-*-* 06:00:00 |
| AccuracySec | 1min |

## Behavior

With `Persistent=true`:
- If the timer fires while the system is off/sleeping, systemd will trigger the service immediately on next boot
- Maximum one catch-up run per missed trigger
- No uncontrolled duplicate if today's canonical run already exists

## Same-Day Duplicate Protection

The pipeline coordinator has built-in missed-run detection:
- `MAX_CATCHUP_RUNS_PER_DAY = 1`
- `detect_missed_runs()` counts incomplete runs from today
- If a completed run exists for today, the catch-up will be limited

## Lock Protection

Even if systemd attempts a catch-up run, the fcntl.flock prevents concurrent execution. If the pipeline is already running, the lock acquisition fails and the service exits cleanly with `LOCK_HELD` status.

## Verdict

No same-day duplicate risk. Persistent=true is safe with the lock + missed-run policy.

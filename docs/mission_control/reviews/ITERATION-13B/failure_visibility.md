# Failure Visibility — Iteration 13B

## How Failures Appear

### Service Failure
- `systemctl status combine-research-daily.service` shows non-zero exit
- `journalctl -u combine-research-daily.service` shows error output
- System health: scheduler component shows DEGRADED if timer inactive

### Lock-Held Refusal
- Script outputs `{"status": "LOCK_HELD", "message": "Another pipeline is active. Safe refusal."}`
- Exit code 0 (intentional — not an error, a safe refusal)
- No duplicate pipeline started

### Pipeline Stage Failure
- Pipeline status set to FAILED in manifest
- `reports/research_pipeline/latest.json` updated with FAILED status
- Health: research_run_contract shows run status as FAILED

### Timer Missed
- Persistent=true triggers catch-up on next boot
- Lock prevents duplicate if pipeline already running
- Health: scheduler shows timer state and next trigger

## No Destructive Auto-Repair

Failed services do NOT trigger automatic recovery that could:
- Restart with different parameters
- Modify eligibility or budget
- Trigger broker operations

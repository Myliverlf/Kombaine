# Recovery Mapping — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED

## Health condition → Recovery procedure mapping

| Health Condition | Severity | Recovery Section | Safe Next Diagnostic |
|-----------------|----------|------------------|---------------------|
| research_run_contract BLOCKED | ERROR | Runbook §1 (Research Lock Stuck) | Check lock file age and PID |
| research_run_contract DEGRADED | WARNING | Runbook §2 (Latest Run Corrupt) | Check manifest status |
| research_run_contract STALE | WARNING | Runbook §2 (Latest Run Corrupt) | Find valid completed run |
| experiment_memory BLOCKED | ERROR | Runbook §3 (Experiment Memory DB) | Check DB existence and integrity |
| knowledge BLOCKED | ERROR | Runbook §4 (Knowledge DB) | Check DB existence and integrity |
| lifecycle BLOCKED | ERROR | Runbook §5 (Lifecycle DB) | Check DB existence and integrity |
| seeder_registry UNSAFE | CRITICAL | Runbook §7 (Registry Parse Failure) | Check JSON validity |
| seeder_registry BLOCKED | ERROR | Runbook §6 (Seeder Handoff) | Check eligible_candidates.json |
| execution DEGRADED | WARNING | Runbook §8 (UNKNOWN Intent) | Query unresolved intents |
| broker DEGRADED | WARNING | Runbook §9 (Broker Unavailable) | Check API connectivity |
| analytics DEGRADED | WARNING | Runbook §10 (Analytics Stale) | Check last reconciliation |
| scheduler WARNING | WARNING | Runbook §11 (Duplicate Owner) | Check systemd + cron overlap |
| disk WARNING/CRITICAL | WARNING/CRITICAL | Runbook §12 (Disk Full) | Check disk usage by directory |
| service CRASH | ERROR | Runbook §13 (Service Crash) | Check journal logs |

## DO NOT recovery

The following are explicitly NOT performed by the health system:
- Automatic broker order placement
- Automatic registry rebuild
- Automatic service restart
- Automatic DB deletion/recreation
- Automatic lock deletion
- Automatic strategy promotion/retirement
- Mode/paper_first changes

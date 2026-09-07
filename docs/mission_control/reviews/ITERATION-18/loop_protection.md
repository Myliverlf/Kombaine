# Loop Protection — Iteration 18

## Limits
- MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD = 3
- REVALIDATION_PERIOD_DAYS = 30
- MAX_RECOVERY_ATTEMPTS = 3
- RECOVERY_BACKOFF_BASE = 60 seconds

## Enforcement
1. Before creating revalidation, check `count_recent_revalidations(strategy_identity)`
2. If count >= MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD, BLOCK the request
3. Dedupe: same strategy + same evidence_type + same source_trigger = duplicate
4. Failed attempts count toward the limit

## State Transitions Blocked
- PENDING → BLOCKED (loop guard triggered)
- No PLANNED/RUNNING state for blocked requests

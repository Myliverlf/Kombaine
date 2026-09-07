# Recovery Playbooks — Iteration 18

## Allowlisted Recovery Actions

| Reason Code | Precondition | Action | Max Attempts | Verification |
|-------------|-------------|--------|--------------|--------------|
| RESEARCH_PIPELINE_FAILURE | Pipeline failed | Restart pipeline | 3 | Health check |
| HEALTH_SNAPSHOT_STALE | Health stale | Rebuild snapshot | 3 | Fresh snapshot |
| LOCK_STALE | Lock > 30 min old | Remove lock file | 3 | Lock acquire |
| TASK_STUCK_RUNNING | Task stuck > threshold | Resume or fail task | 3 | Task state check |
| DB_UNAVAILABLE_RETRY | DB temporarily unavailable | Retry query | 3 | Query success |
| KNOWLEDGE_BUILD_FAILED | Knowledge build failed | Retry build | 3 | Build output |

## Non-Allowlisted (Must Escalate)
- MODE_CHANGE
- REGISTRY_MUTATION
- SWAP_EXECUTION
- BROKER_ORDER
- RISK_CHANGE
- ELIGIBILITY_CHANGE
- ARBITRARY_SHELL_COMMAND

## Recovery Flow
1. Attempt recovery (max_attempts enforced)
2. Verify recovery via health check
3. If verified: RESOLVED
4. If failed after max attempts: ESCALATED to operator

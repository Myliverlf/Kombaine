# Task Model — Iteration 18

## Task States
```
PENDING → RUNNING → COMPLETED
                    → FAILED
          → BLOCKED
          → CANCELLED
          → SUPERSEDED
```

## Task Fields
- task_id: unique identifier
- task_type: allowlisted type (CANONICAL_RESEARCH, REVALIDATION, DATA_VALIDATION, etc.)
- source_cycle: MC cycle that created the task
- priority: P0-P7
- payload_hash: deterministic hash for deduplication
- state: current state
- attempts: retry count
- max_attempts: default 3
- created_at, started_at, finished_at: timestamps
- result_ref, error_ref: references to results/errors
- decision_id: links to MC decision
- revalidation_id: links to revalidation request

## Idempotency
- Deterministic payload hash prevents duplicate tasks
- Same payload_hash + same state = duplicate detected
- Completed tasks are never re-executed

## Restart/Resume
- After process restart, RUNNING tasks are visible via get_pending_tasks()
- Tasks stuck in RUNNING can be resumed or marked FAILED
- PENDING tasks can be restarted idempotently

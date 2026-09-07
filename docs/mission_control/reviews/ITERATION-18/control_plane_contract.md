# Control Plane Contract — Iteration 18

## Purpose
Single deterministic control-plane owner above all analytical modules.

## Invariants
1. One primary action per cycle (observes many, acts on one)
2. Zero registry/swap/broker/execution/signal mutation
3. Deterministic policy (no LLM-authoritative dispatch)
4. Task deduplication and idempotency
5. Restart/resume after process restart
6. Bounded revalidation with loop protection
7. Incident deduplication with fingerprint
8. Recovery via allowlisted playbooks only
9. Alert rate limiting and secret redaction
10. Human review: can create, CANNOT approve

## Allowed Actions
```
NO_ACTION
OPEN_INCIDENT
CREATE_REVALIDATION
RUN_CANONICAL_RESEARCH
REQUEST_HUMAN_REVIEW
WAIT_FOR_EVIDENCE
ESCALATE_OPERATOR
RESUME_FAILED_TASK
```

## Forbidden Actions
```
orders
position closes
strategy swaps
registry promotion/deactivation
capital reallocation
risk changes
live activation
mode changes
eligibility changes
```

## State Store
`state/mission_control.db` — control-plane truth only

## Lock
`state/.mission_control.lock` — non-blocking, stale recovery (30 min)

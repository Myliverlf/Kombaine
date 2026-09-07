# Abort Conditions — Iteration 23

**Date:** 2026-08-30

## Hard Abort Triggers

| # | Condition | Detection | Response |
|---|-----------|-----------|----------|
| 1 | Broker truth UNKNOWN | Live query fails or returns unexpected state | ABORT |
| 2 | Reconciliation CONFLICTED | Broker ↔ local mismatch | ABORT |
| 3 | Duplicate order/intent suspicion | Same intent submitted twice | ABORT |
| 4 | Risk Gate unhealthy | Risk system unavailable or returning errors | ABORT |
| 5 | Stale/bad data | Data older than policy or integrity check fails | ABORT |
| 6 | Unexpected position | Broker has position not in local state | ABORT |
| 7 | Stale authorization | Authorization expired or state changed | ABORT |
| 8 | Strategy/evidence mismatch | Strategy evidence no longer valid | ABORT |
| 9 | Unresolved execution UNKNOWN | Order state UNKNOWN beyond policy timeout | ABORT |
| 10 | Critical MC incident | Mission Control health check fails | ABORT |
| 11 | Source-of-truth mismatch | Multiple conflicting sources of truth | ABORT |

## Abort Semantics

### What Abort Means
```
NO NEW ORDERS
ESCALATE HUMAN
PRESERVE TRUTH
```

### What Abort Does NOT Mean
- **NO automatic liquidation** — Never auto-close positions
- **NO position mutation** — Never change positions without human
- **NO order cancellation** — Never auto-cancel without human
- **NO mode change** — Never switch modes without human

### Abort Process
1. Detect abort condition
2. Stop all new order submissions
3. Record abort reason and timestamp
4. Preserve all evidence (logs, state, broker queries)
5. Escalate to human operator via Telegram (if configured) or other channel
6. Wait for human resolution
7. Human decides: fix issue, manually intervene, or continue halted

### Post-Abort Recovery
1. Human reviews abort reason
2. Human resolves underlying issue
3. Human explicitly re-authorizes
4. System re-validates all preconditions
5. Trading resumes only after full re-validation

## Critical Rule
Abort is a SAFETY mechanism, not a recovery mechanism. It stops the system and preserves state. Recovery requires human intervention.

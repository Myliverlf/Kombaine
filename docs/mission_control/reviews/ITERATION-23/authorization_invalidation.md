# Authorization Invalidation — Iteration 23

**Date:** 2026-08-30

## Invalidation Triggers

### Material Changes (Immediate Invalidation)
1. **Certificate change** — Any material change to Iteration 23 certification
2. **Snapshot change** — prelive_snapshot_id no longer matches
3. **Strategy change** — Strategy evidence no longer valid
4. **Instrument change** — Instrument no longer in certified scope
5. **Account change** — Account ID changed or credentials invalid
6. **Risk policy change** — Risk parameters modified
7. **Allocation policy change** — Allocation parameters modified
8. **Execution policy change** — Execution parameters modified

### Health Changes (Immediate Invalidation)
9. **Critical health incident** — Any blocking incident detected
10. **Scheduler failure** — combine-research-daily.timer fails
11. **Data degradation** — Certified data becomes stale/invalid
12. **Broker truth change** — Broker state unknown or conflicted

### Time-Based Invalidation
13. **Expiry** — Authorization has bounded lifetime
14. **Staleness** — Authorization older than policy allows

## Invalidation Process
1. Detect invalidation trigger
2. Record trigger and timestamp
3. Mark authorization as INVALID
4. No new orders allowed
5. Escalate to human operator
6. Preserve all evidence

## No "Approve Latest"
Authorization binds to EXACT state at time of issuance. If state changes, old authorization is invalid. Must re-authorize with new state.

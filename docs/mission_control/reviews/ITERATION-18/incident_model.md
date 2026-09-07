# Incident Model — Iteration 18

## Severity Levels
- INFO: Normal operational events
- WARNING: Something degraded but not blocking
- DEGRADED: System degraded, needs attention
- BLOCKING: System blocked, immediate attention needed
- SAFETY: Trading safety invariant violated

## States
```
OPEN → ACKNOWLEDGED → RECOVERING → RESOLVED
     → ESCALATED
     → SUPPRESSED
```

## Deduplication
- Fingerprint = SHA256(source_component:reason_code)[:16]
- Same fingerprint = same incident (increment occurrence_count)
- Different fingerprint = different incident

## Sources
System Health, research pipeline failure, DB corruption, stale data,
lock failure, scheduler failure, source mismatch, attribution contradiction,
review integrity failure, MC task failure

## Forbidden Incident Triggers
- Zero eligible candidates (normal state)
- Zero review cases (normal state)
- NO_ACTION decision (normal state)

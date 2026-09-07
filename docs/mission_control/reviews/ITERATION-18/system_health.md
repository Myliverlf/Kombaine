# System Health — Mission Control Integration

## MC Health Component
Mission Control adds a control_plane_health domain to System Health.

## Health Fields
- last cycle timestamp
- policy version
- lock status (free / held / stale)
- pending tasks count
- failed tasks count
- open incidents count
- escalated incidents count
- pending revalidations count
- alert subsystem status
- scheduler status

## States
- HEALTHY: MC cycling normally
- DEGRADED: Some issues but functional
- BLOCKED: Cannot cycle (lock, DB, etc.)
- STALE: Last cycle too old
- UNSAFE: Safety invariant violated
- UNKNOWN: Cannot determine health

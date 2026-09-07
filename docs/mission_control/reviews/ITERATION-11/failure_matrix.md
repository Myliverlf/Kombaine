# Failure Matrix — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED — 85 tests pass

## F1–F24 Summary

| ID | Scenario | Domain Health | Overall Health | Alert Severity | Trading Affected | Auto-Repair |
|----|----------|---------------|----------------|----------------|------------------|-------------|
| F1 | Config missing | control_plane BLOCKED | DEGRADED | CRITICAL | NO | NO |
| F2 | Invalid mode | control_plane DEGRADED | DEGRADED | WARNING | NO | NO |
| F3 | Research latest missing | research STALE | STALE | WARNING | NO | NO |
| F4 | Research run corrupt | research DEGRADED | DEGRADED | WARNING | NO | NO |
| F5 | Experiment Memory DB unavailable | experiment_memory BLOCKED | DEGRADED | ERROR | NO | NO |
| F6 | Knowledge DB unavailable | knowledge BLOCKED | DEGRADED | ERROR | NO | NO |
| F7 | Lifecycle DB unavailable | lifecycle BLOCKED | DEGRADED | ERROR | NO | NO |
| F8 | Registry malformed | selection UNSAFE | UNSAFE | CRITICAL | NO | NO |
| F9 | Legacy seeder default enabled | invariant FAIL | DEGRADED | WARNING | NO | NO |
| F10 | Foreign ticker invariant | invariant FAIL | UNSAFE | CRITICAL | NO | NO |
| F11 | Execution journal unavailable | execution BLOCKED | DEGRADED | ERROR | NO | NO |
| F12 | Unresolved execution UNKNOWN | execution DEGRADED | DEGRADED | WARNING | NO | NO |
| F13 | Broker evidence unavailable | broker DEGRADED | DEGRADED | WARNING | NO | NO |
| F14 | Analytics stale | analytics BLOCKED | DEGRADED | WARNING | NO | NO |
| F15 | Disk low | disk WARNING/CRITICAL | varies | WARNING/CRITICAL | NO | NO |
| F16 | Report path unwritable | control_plane DEGRADED | DEGRADED | WARNING | NO | NO |
| F17 | Duplicate scheduler owner | scheduler WARNING | DEGRADED | WARNING | NO | NO |
| F18 | Lock contention | component BLOCKED | DEGRADED | WARNING | NO | NO |
| F19 | Component import failure | violation detected | varies | ERROR | NO | NO |
| F20 | Health check itself crashes | N/A | N/A | CRITICAL | NO | NO |
| F21 | Secret in diagnostic input | redaction test | N/A | CRITICAL | NO | NO |
| F22 | Stale dependency chain | upstream affected | DEGRADED | WARNING | NO | NO |
| F23 | Source-of-truth multiple writers | store flagged | varies | P0/P1/P2 | NO | NO |
| F24 | Service alive but job stale | component STALE | DEGRADED | WARNING | NO | NO |

## Key invariants verified

- Automatic destructive repair: **NO** for all scenarios
- Real broker orders created: **NO** for all scenarios
- Trading affected: **NO** for all scenarios

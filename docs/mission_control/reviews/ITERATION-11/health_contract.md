# Health Contract — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED

## HealthStatus enum

| Status | Meaning |
|--------|---------|
| HEALTHY | Component available + prerequisites valid + state fresh |
| DEGRADED | Available but operating below normal (e.g., in-progress run, minor issues) |
| BLOCKED | Cannot safely perform intended function (e.g., DB missing) |
| STALE | Available but evidence/input age exceeds policy |
| UNSAFE | Invariant violation with potential safety consequence |
| UNKNOWN | Status cannot be determined |

## Domain health aggregation

Domains aggregate component health using worst-status priority:
UNSAFE > BLOCKED > DEGRADED > STALE > UNKNOWN > HEALTHY

## Fault isolation

| Failure | Domain affected | Domains NOT affected |
|---------|----------------|---------------------|
| Knowledge DB down | knowledge_health BLOCKED | execution_health, broker_health, analytics_health |
| Research stale | research_health STALE | execution_health, broker_health, risk_health |
| Experiment Memory down | experiment_memory_health BLOCKED | execution_health, broker_health |
| Lifecycle stale | lifecycle_health STALE | execution_health, broker_health |
| Broker unavailable | broker_health BLOCKED | research_health, knowledge_health |
| Analytics stale | analytics_health DEGRADED | execution_health, broker_health |

## Invariant checks

| ID | Description | Severity | Check |
|----|-------------|----------|-------|
| INV-001 | paper_first unchanged | CRITICAL | config.json paper_first == True |
| INV-002 | Foreign ticker not active | CRITICAL | universe == allowed_universe |
| INV-003 | Strategy cannot bypass risk | INFO | paper mode enforced |
| INV-004 | No silent legacy fallback | WARNING | .use_legacy_seeder flag absent |
| INV-005 | Latest pointer references COMPLETED run | WARNING | latest_run.json → manifest status COMPLETED |
| INV-006 | Knowledge cannot mutate registry | INFO | code inspection |
| INV-007 | Lifecycle cannot mutate swap | INFO | code inspection |
| INV-008 | UNKNOWN intent cannot auto-resubmit | INFO | execution_journal design |
| INV-009 | Health check is read-only | INFO | code inspection |

## Health report persistence

- `reports/system_health/latest.json` — atomic pointer
- `reports/system_health/latest.md` — human-readable
- `reports/system_health/history/{timestamp}.json` — append-only, max 100 entries

## Alert policy

| Severity | Example |
|----------|---------|
| INFO | Health check completed |
| WARNING | Stale knowledge, unresolved intents |
| ERROR | Research blocked, DB integrity check failed |
| CRITICAL | paper_first changed, registry malformed, foreign ticker |

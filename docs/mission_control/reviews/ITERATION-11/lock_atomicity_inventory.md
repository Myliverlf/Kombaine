# Lock & Atomicity Inventory — Iteration 11

**Date:** 2026-08-30
**Status:** VERIFIED

## Lock Mechanisms

| Store | Lock Type | Owner | Scope | Timeout | Stale Behavior |
|-------|-----------|-------|-------|---------|----------------|
| state/.research.lock | fcntl/flock | run_contract | Per-run isolation | none (manual release) | Stale lock = research BLOCKED; manual removal required |
| state/.supervisor.lock | fcntl/flock | supervisor | Per-cycle isolation | none (manual release) | Stale lock = supervisor skip; auto-release on process exit |
| state/experiment_memory.db | sqlite_transaction | experiment_memory | Per-write | none | Transaction rollback on error |
| state/research_knowledge.db | sqlite_transaction | research_knowledge | Per-write | none | Transaction rollback on error |
| state/strategy_lifecycle.db | sqlite_transaction | strategy_lifecycle | Per-write | none | Transaction rollback on error |
| analytics.db | sqlite_transaction | execution_journal, analytics | Per-write | none | Transaction rollback on error |
| state/strategy_registry.json | atomic file write | seeder_registry | Per-write | none | Previous state preserved on failure |

## Critical Mutable Stores Without Coordination

| Store | Issue | Severity | Mitigation |
|-------|-------|----------|------------|
| state/portfolio.json | Multiple writers (supervisor, engine) | P2 | Supervisor-level lock covers both; sequential access within supervision cycle |
| state/signal_pool.json | Derived view, regenerated from registry | P2 | Not canonical; regenerated on demand |

## Atomic Write Policy

### JSON/File stores
```
write temp → fsync → atomic rename (os.replace)
```
Implemented in: `core/system_health.py` (health reports), `core/run_contract.py` (run bundles)

### SQLite stores
```
transaction → commit/rollback
```
Standard sqlite3 transaction handling in all DB-writing modules.

## Multiple-Writer Detection

| Store | Writers | Can Corrupt? | Status |
|-------|---------|-------------|--------|
| strategy_registry.json | seeder_registry (single canonical) | No — single writer | CLEAN |
| portfolio.json | supervisor, engine | Low — covered by supervisor lock | MONITORED |
| analytics.db | execution_journal, analytics | Low — append-only for intents | MONITORED |

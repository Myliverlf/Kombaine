# ADR-2026-08-30: System Modularity & Operability Foundation

**Status:** ACCEPTED
**Date:** 2026-08-30
**Iteration:** 11
**Change class:** CLASS 2 — Runtime non-trading / operability infrastructure

---

## Context

The strategy_combine system has grown through Iterations 01–10 into a multi-layer architecture with canonical data, research, knowledge, selection, signal, risk, execution, broker, and analytics domains. While each layer has been individually validated, the system lacked:

1. **Explicit module boundaries** — no formal contracts defining what each module owns, reads, writes, and is forbidden from doing
2. **Dependency graph** — no machine-readable map of inter-module dependencies
3. **Source-of-truth matrix** — no canonical registry of which store is authoritative vs derived
4. **Scheduler ownership** — no inventory of who schedules what, with duplicate detection
5. **Lock/atomicity inventory** — no systematic view of write coordination
6. **Unified health observability** — no way to answer "what is healthy, degraded, blocked, stale, or unsafe right now?"
7. **Recovery procedures** — no documented bounded recovery for common failure scenarios

This gap creates a failure mode: the system may be logically correct in isolated modules while becoming difficult to inspect, coordinate, recover, and operate safely as a whole.

## Decision

### Module Boundary Contracts (core/module_contracts.py)

Define explicit `ModuleContract` dataclasses for all 12 major modules:

| Module | Owner | Domain | Broker Capable | Registry Mutator |
|--------|-------|--------|----------------|------------------|
| run_contract | core/run_contract.py | RESEARCH | NO | NO |
| experiment_memory | core/experiment_memory.py | EXPERIMENT_MEMORY | NO | NO |
| novelty_gate | core/novelty_gate.py | NOVELTY | NO | NO |
| research_knowledge | core/research_knowledge.py | KNOWLEDGE | NO | NO |
| strategy_lifecycle | core/strategy_lifecycle.py | LIFECYCLE | NO | NO |
| seeder_handoff | core/seeder_handoff.py | SELECTION | NO | NO |
| seeder_registry | core/strategy_registry.py | SELECTION | NO | YES |
| signal_pool | code/signal_pool_exporter.py | SIGNAL | NO | NO |
| risk_manager | core/risk.py | RISK | NO | NO |
| execution_journal | core/execution_journal.py | EXECUTION | NO | NO |
| broker_evidence | core/broker_evidence.py | BROKER | READ-ONLY | NO |
| analytics | core/analytics.py | ANALYTICS | NO | NO |
| supervisor | core/supervisor.py | CONTROL_PLANE | INDIRECT | NO |
| engine | core/engine.py | EXECUTION | YES (VETO) | NO |

**Key forbidden dependencies:**
- `research_knowledge` must NOT import broker, tinkoff, execution_journal, engine
- `strategy_lifecycle` must NOT import broker, tinkoff, engine, seeder
- `experiment_memory` must NOT import broker, tinkoff, engine, strategy_registry
- `novelty_gate` must NOT import broker, tinkoff, engine, strategy_registry

### Dependency Graph (core/module_contracts.py)

Machine-readable `DependencyEdge` list with 25 edges covering:
- `reads` — component reads from a store
- `writes` — component writes to a store
- `calls` — component invokes another component
- `scheduled_by` — component is triggered by a scheduler

No cycles detected in the critical path (research → knowledge → lifecycle → selection).

### Source-of-Truth Matrix (core/module_contracts.py)

12 entries covering all critical domains:

| Domain | Canonical Source | Status |
|--------|-----------------|--------|
| Broker positions/fills/money | Tinkoff broker API | VERIFIED |
| Execution intents | analytics.db execution_intents | VERIFIED |
| Analytics trades | analytics.db | VERIFIED |
| Strategy lifecycle registry | state/strategy_registry.json | VERIFIED |
| Canonical research runs | reports/strategy_architect/runs/{run_id}/manifest.json | VERIFIED |
| Experiment Memory | state/experiment_memory.db | VERIFIED |
| Research Knowledge | state/research_knowledge.db | VERIFIED |
| Lifecycle observations | state/strategy_lifecycle.db | VERIFIED |
| Signal pool / waitlist | DERIVED from strategy_registry.json | DERIVED |
| Latest pointers | DERIVED — latest_run.json | DERIVED |
| Runtime configuration | config.json (runtime-parsed) | VERIFIED |
| Portfolio operational state | state/portfolio.json | VERIFIED |

**Multiple-writer detection:** signal_pool.json is written by signal_pool_exporter (derived); portfolio.json has multiple writers (supervisor, engine) — flagged as P2.

### Scheduler Ownership

Inventory of all schedulers:
- **combine-15m.timer** → data download (systemd, SINGLE_OWNER)
- **combine-seeder.timer** → seeder (systemd, SINGLE_OWNER)
- **combine-supervisor.timer** → supervisor loop (systemd, SINGLE_OWNER)
- **supervisor_loop** → internal sleep loop (internal, SINGLE_OWNER)
- Manual entrypoints: strategy_architect_autopilot, seeder

**No duplicate ownership detected.** Each job has exactly one scheduling owner.

### Lock/Atomicity Policy

| Store | Lock Mechanism | Scope |
|-------|---------------|-------|
| state/.research.lock | fcntl/flock | Research run isolation |
| state/.supervisor.lock | fcntl/flock | Supervision cycle isolation |
| state/experiment_memory.db | sqlite_transaction | DB write isolation |
| state/research_knowledge.db | sqlite_transaction | DB write isolation |
| state/strategy_lifecycle.db | sqlite_transaction | DB write isolation |
| analytics.db | sqlite_transaction | DB write isolation |
| state/strategy_registry.json | atomic file write | Registry isolation |

### Unified Health Model (core/system_health.py)

**HealthStatus enum:** HEALTHY | DEGRADED | BLOCKED | STALE | UNSAFE | UNKNOWN

**Domain health keys:**
- research_health, knowledge_health, lifecycle_health, experiment_memory_health
- novelty_health, selection_health, signal_health, risk_health
- execution_health, broker_health, analytics_health, control_plane_health, disk_health

**Overall health priority:** UNSAFE > BLOCKED > DEGRADED > STALE > UNKNOWN > HEALTHY

**Fault isolation:** Research/knowledge degradation does NOT make execution/broker UNSAFE.
A stale knowledge report does NOT mean broker execution is unsafe.

**Read-only invariant:** Health checker performs zero mutations:
- No broker calls
- No registry writes
- No DB modifications
- No mode changes
- No service restarts
- Only writes to `reports/system_health/` (derived reports)

### Invariant Checks

9 invariants monitored:
1. **INV-001:** paper_first unchanged unless explicitly authorized
2. **INV-002:** Foreign ticker not active/signal/promotion/swap
3. **INV-003:** Strategy cannot bypass risk to broker
4. **INV-004:** Canonical seeder does not silently use legacy
5. **INV-005:** Latest research pointer references COMPLETED valid run
6. **INV-006:** Knowledge cannot mutate registry
7. **INV-007:** Lifecycle observer cannot mutate swap state
8. **INV-008:** UNKNOWN execution intent cannot auto-resubmit
9. **INV-009:** Health check system is read-only

### Recovery Boundary

Documented bounded recovery in `docs/mission_control/OPERATIONS_RUNBOOK.md` covering 13 scenarios:
1. Research lock stuck
2. Latest run corrupt
3. Experiment Memory DB unavailable
4. Knowledge DB unavailable
5. Lifecycle DB unavailable
6. Seeder handoff blocked
7. Registry parse/write failure
8. Execution UNKNOWN intent
9. Broker unavailable
10. Analytics reconciliation stale
11. Duplicate scheduler owner
12. Disk full / low disk
13. Service crash / restart loop

**Hard boundary:** No destructive auto-repair. Recovery is safe diagnostics + bounded manual steps.

### Secret Safety

Health reports/logs never expose:
- broker tokens
- API keys
- Telegram tokens
- credentials

All diagnostic output is redacted before persistence via `_redact_secrets()`.

### Production Research Readiness

Health check assesses readiness but does NOT start production research.
Assessment: YES / NO / CONDITIONAL with explicit blockers.

Prerequisites checked:
- Data readiness
- Research run contract
- Scheduler ownership
- Disk capacity
- Experiment Memory availability
- Novelty availability
- Knowledge downstream availability
- Seeder canonical handoff
- Lock health
- Report paths

## Consequences

### Positive
- System is now inspectable: "What is healthy right now?" has a deterministic answer
- Module boundaries are explicit: forbidden dependencies are tested
- Source-of-truth conflicts are detectable
- Scheduler collisions are detectable
- Recovery procedures are documented and bounded
- Failure isolation is proven: research degradation ≠ execution unsafe
- Secret safety is tested

### Negative
- Health check adds a new module to maintain
- Some invariant checks are structural (code-level) rather than runtime-verified
- Production research readiness is assessed but not started

### Risks
- Health check itself could become stale if not run periodically
- Invariant checks may need updating as the system evolves
- Recovery procedures require human judgment (by design)

## Evidence

- 85 new tests (T1-T23, F1-F24) all green
- Core modules remain importable
- Health report generation verified
- Secret redaction verified
- Atomic write verified
- Read-only invariant verified

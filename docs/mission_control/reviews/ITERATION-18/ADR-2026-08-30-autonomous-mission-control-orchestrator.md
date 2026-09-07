# ADR-2026-08-30: Autonomous Mission Control Orchestrator

## Status
Accepted — Iteration 18

## Date
2026-08-30

## Context

Before Iteration 18, the strategy_combine system contained many capable analytical modules (system health, research pipeline, strategy lifecycle, performance attribution, market regime, replacement ranking, human review) but lacked a single deterministic control-plane owner to coordinate them. Each module operated independently, producing recommendations and observations but with no unified answer to:

> What bounded action, if any, does the system need next?

## Decision

Create a **single Mission Control control-plane owner** that:

1. **Observes** the entire system through canonical sources
2. **Classifies** state via a deterministic snapshot
3. **Evaluates** against a versioned priority policy
4. **Chooses ONE** bounded non-trading action per cycle
5. **Executes** only authorized non-trading task types
6. **Tracks** task lifecycle with deduplication and restart/resume
7. **Detects** failure and recovers safely
8. **Escalates** when recovery is insufficient
9. **Reports** evidence through an audit trail

### Key Architectural Decisions

#### 1. One Primary Action Per Cycle
Each MC cycle observes many potential problems but selects exactly ONE primary action. This prevents fragmented, competing actions and ensures deterministic behavior.

Allowed actions (non-trading only):
- `NO_ACTION` — system nominal
- `OPEN_INCIDENT` — operational failure detected
- `CREATE_REVALIDATION` — evidence refresh needed
- `RUN_CANONICAL_RESEARCH` — research pipeline trigger
- `REQUEST_HUMAN_REVIEW` — replacement candidate needs human decision
- `WAIT_FOR_EVIDENCE` — waiting for evidence refresh
- `ESCALATE_OPERATOR` — operator intervention needed
- `RESUME_FAILED_TASK` — retry failed non-trading task

#### 2. Deterministic Priority Policy
Priority order (P0-P7, lower = higher priority):
- P0: Safety / blocked / unsafe
- P1: Data or source-of-truth failure
- P2: Failed or stale control-plane task
- P3: Required revalidation
- P4: Canonical research need
- P5: Human review need
- P6: Wait for evidence
- P7: No action

Policy is versioned (`MC_POLICY_VERSION = "1.0.0"`) and deterministic — no LLM-authoritative dispatch.

#### 3. Zero Trading Mutation
Mission Control NEVER executes:
- Orders or position changes
- Strategy swaps or activation/deactivation
- Registry promotion/deactivation
- Capital reallocation
- Risk limit changes
- Mode or eligibility changes

#### 4. State Store
Dedicated SQLite store: `state/mission_control.db`
Tables: mc_cycles, mc_snapshots, mc_decisions, mc_tasks, mc_task_events, mc_incidents, mc_revalidation_requests, mc_alert_events, mc_policy_versions

#### 5. Lock Mechanism
File-based lock: `state/.mission_control.lock`
- Non-blocking acquire (second caller gets False)
- Stale recovery: lock older than 30 minutes is automatically recovered
- PID tracking for diagnostics

#### 6. Revalidation Engine
- Bounded experiment plans with exact strategy, timeframe, methodology
- Loop protection: max 3 revalidations per strategy per 30-day period
- Dedupe check for identical requests
- Evidence types: BACKTEST, WALK_FORWARD, PAPER, REGIME_SPECIFIC, DATA_REFRESH, CORRELATION, METHODOLOGY

#### 7. Incident Manager
- Fingerprint-based deduplication
- Severity: INFO, WARNING, DEGRADED, BLOCKING, SAFETY
- Recovery allowlist: only predefined playbooks
- Verification: health check after recovery
- Escalation: structured package to operator

#### 8. Observability Layer
- Event types: MC_DECISION, TASK_CREATED/STARTED/COMPLETED/FAILED, REVALIDATION_CREATED/COMPLETED, INCIDENT_OPENED/ESCALATED/RESOLVED, HUMAN_REVIEW_REQUIRED, SYSTEM_BLOCKED
- Rate limiting: max 10 alerts per fingerprint per cycle
- Secret redaction: tokens, keys, API keys automatically redacted

#### 9. Human Review Integration
- MC may CREATE human review cases via canonical interface
- MC CANNOT approve, reject, defer, or impersonate human decisions
- Human review remains governed exclusively by Iteration 17

#### 10. Scheduler Ownership
- Existing canonical research timer (combine-research-daily.timer at 06:00) is NOT modified
- MC runs on-demand or after major pipeline events
- No duplicate scheduler owner

## Consequences

### Positive
- Single source of truth for control-plane decisions
- Deterministic, auditable behavior
- No trading mutation risk
- Clear escalation path
- Task lifecycle with restart/resume

### Negative
- Additional SQLite database to maintain
- Lock contention possible (mitigated by non-blocking + stale recovery)
- Additional complexity in the control plane

## Evidence
- 70 tests passing (T1-T24, F1-F24, integration, regression)
- Zero registry/swap/broker/execution/signal mutation in audit
- Lock concurrency verified (first acquire, second refuses, stale recovery)
- Real MC cycle proven against current paper state

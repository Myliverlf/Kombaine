# ADR-2026-08-30: Controlled Paper Portfolio Execution

## Status
ACCEPTED

## Date
2026-08-30

## Context
After Iterations 01–19 established research, lifecycle, ranking, human review, and production truth, there was no single authoritative transition path for portfolio changes. Multiple legacy swap paths existed without unified governance. The system needed a controlled, auditable, paper-only transition mechanism.

## Decision
Implement `core/portfolio_transition.py` as the single authoritative controlled transition path for all portfolio changes. Key design decisions:

### 1. APPROVE ≠ EXECUTE
Governance APPROVE (Iteration 17) does NOT silently mean permission to mutate portfolio state. A separate explicit HUMAN paper execution authorization is required. Agent/system cannot create valid human authorization.

### 2. Paper Only (CLASS 2)
All transition paths are PAPER ONLY. Zero real broker mutation from any code path. No Tinkoff SDK imports. No order placement. No position close.

### 3. Deterministic State Machine
18-state machine: CREATED → PREFLIGHT → READY_PAPER → DRAINING/WAITING_FLAT → VERIFYING_FLAT → DEACTIVATING_INCUMBENT → ACTIVATING_CANDIDATE → RECONCILING → OBSERVING → COMPLETED.

No direct APPROVED → ACTIVE jump. Crash recovery inspects state before resuming.

### 4. Deterministic Allocation
Versioned, deterministic `AllocationPolicy` with explicit limits. Risk gate remains final authority for individual trades. Unknown correlation is UNKNOWN, never zero.

### 5. Open Position Safety
If incumbent owns an open position, candidate cannot activate. No real close order is authorized. WAITING_FLAT is a valid terminal waiting state.

### 6. Atomicity & Exclusivity
One transition per approval+slot+evidence_hash. Concurrent transitions for same slot → BLOCKED_CONFLICT. Duplicate prevention via approval_evidence_hash + slot_id uniqueness.

## Consequences
- Single authoritative transition path — legacy parallel swap paths routed through it
- Full audit trail with versioned policy versions
- Crash recovery without blind replay
- Rollback never places real trades
- Reconciliation mandatory before COMPLETED
- Telegram alerts informational only — cannot approve

## Evidence
- 85 tests (T1–T24 + F1–F24) all GREEN
- Zero real broker mutation from any Iteration-20 path
- Broker safety check confirms no SDK imports

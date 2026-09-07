# Evidence Bundle — Iteration 20

## Transition Authority
- Single authoritative path established in `core/portfolio_transition.py`
- Legacy swap paths inventoried: `core/engine.py` (live execution), `code/strategy_registry.py` (registry bridge)
- Transition Manager does not place strategy orders

## Approval Bridge
- `ApprovalBridge.validate_approval()` — only accepts HUMAN actor + APPROVE decision
- `ApprovalBridge.validate_authorization()` — separate HUMAN paper execution authorization
- Agent/system approval rejected: BLOCKED_AGENT_APPROVAL, BLOCKED_SYSTEM_APPROVAL

## Execution Authorization
- Separate `ExecutionAuthorization` dataclass
- Requires: HUMAN actor, AUTHORIZE_PAPER_TRANSITION action, matching plan_hash
- Stored in `execution_authorizations` SQLite table

## Allocation Policy
- `AllocationPolicy` with versioned deterministic computation
- Checks: total exposure, single strategy, reserve, concentration
- Risk gate remains final authority

## State Machine
- 18 states with validated transitions
- TERMINAL_STATES: COMPLETED, ROLLED_BACK, ESCALATED, CANCELLED, BLOCKED
- No direct APPROVED → ACTIVE jump

## Atomicity / Idempotency
- One transition per approval+slot+evidence_hash
- Concurrent transitions: BLOCKED_CONFLICT
- Duplicate prevention via unique constraint

## Open Position Rule
- Open position → WAITING_FLAT
- No real close order authorized
- Candidate cannot activate until safe

## Crash Recovery
- Inspects actual state, resumes/rollbacks safely
- No blind replay
- Checkpoints at each critical state

## Rollback
- Never places real trade
- Records reason, checkpoint, snapshot
- FAILED → ROLLING_BACK → ROLLED_BACK

## Reconciliation
- Mandatory before COMPLETED
- Checks: DB, approval, authorization, slot, paper position, execution intent, health
- Results: CONSISTENT, DEGRADED, CONFLICTED, INCOMPLETE

## Broker Safety
- Zero Tinkoff SDK imports in portfolio_transition.py
- Zero broker-mutating calls
- All paths are DB_WRITE_ONLY or COMPUTE_ONLY

## Test Coverage
- 85 tests: T1-T24 + F1-F24 + safety proofs
- All GREEN

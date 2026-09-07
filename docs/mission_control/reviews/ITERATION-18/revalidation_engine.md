# Revalidation Engine — Iteration 18

## Purpose
Bounded control-plane revalidation capability with loop protection.

## Request Fields
- revalidation_id: unique identifier
- strategy_identity: strategy/config identity
- reason: why revalidation is needed
- requested_evidence_type: one of BACKTEST/WALK_FORWARD/PAPER/DATA_REFRESH/CORRELATION/METHODOLOGY/REGIME_SPECIFIC
- source_trigger: what triggered the request (LIFECYCLE, HUMAN_REVIEW, MC_DECISION, etc.)
- source_case_id, source_build_id: provenance
- priority: P0-P7
- state: PENDING → PLANNED → RUNNING → COMPLETED/FAILED/BLOCKED/CANCELLED/SUPERSEDED

## Loop Protection
- Max 3 automatic revalidations per strategy per 30-day period
- Same reason + same evidence hash dedupe
- Failed attempts counted toward limit
- No immediate recursive revalidation

## Experiment Plan
Each request gets a bounded plan:
- Exact strategy/config identity
- Exact dataset/timeframe
- Exact methodology
- Max work budget (1h default)
- Expected artifacts
- Completion criteria

## Evidence Types
- BACKTEST_REVALIDATION
- WALK_FORWARD_REVALIDATION
- PAPER_EVIDENCE_REFRESH
- REGIME_SPECIFIC_REVALIDATION
- DATA_REFRESH
- CORRELATION_REVALIDATION
- METHODOLOGY_REVALIDATION

# ADR-2026-08-30-canonical-consumer-migration-and-risk-enforcement-alignment

**Status:** Accepted
**Date:** 2026-08-30
**Iteration:** 23H
**Deciders:** Hermes Agent (Mission Control)

---

## Context

Iteration 23G created canonical truth artifacts:
- `config/research_qualification_policy.json` — single qualification policy
- `core/horizon_resolution.py` — canonical horizon resolver
- `core/eligible_candidates_contract.py` — immutable handoff contract
- `docs/mission_control/canonical_truth.json` — truth hierarchy

However, the codebase had 26 production-reachable consumers that did NOT use these canonical sources:
- 7 modules with hardcoded qualification thresholds (some weaker, some stronger than canonical)
- `data_loader.py` bypassed the canonical horizon resolver with its own `_candidate_csv_paths()`
- Risk policy in `config.json` (risk_per_trade_pct=2.7, max_slots=2) differed from `LIVE_RISK_V1` (0.25%, 1 position)
- No programmatic stage transition machine existed
- Legacy handoff paths were still reachable

## Decision

Create three new canonical infrastructure modules and migrate all consumers:

### 1. `core/canonical_policy_loader.py`
- Loads `config/research_qualification_policy.json`
- Provides `get_threshold()`, `policy_hash()`, `policy_version()`
- Fail-closed if policy missing/invalid

### 2. `core/risk_policy_loader.py`
- Loads `state/live_risk/LIVE_RISK_V1.json`
- Provides `get_risk_policy()`, `check_risk_qualification()`
- Validates shorting/leverage/positions against canonical policy

### 3. `core/stage_machine.py`
- Programmatic stage transition graph (9 stages, linear chain)
- Enforces: no stage skips, 60d-only cannot reach PAPER, evidence required
- Immutable evidence contracts per transition

### Consumer Migrations
- `code/data_loader.py` → uses `resolve_horizon()` as primary path
- `code/qualification_campaign.py` → loads thresholds from canonical policy
- `core/research_pipeline.py` → loads thresholds from canonical policy
- `code/candidate_gate.py` → canonical-aware (keeps stricter local gate)
- `code/quality_gate.py` → canonical-aware (keeps stricter local gate)
- `iteration12_proof.py` → loads thresholds from canonical policy

## Consequences

### Positive
- Single source of truth for qualification thresholds
- Horizon resolution is canonical for all data loading
- Risk qualification checks match LIVE_RISK_V1
- Stage transitions are machine-enforced
- Policy identity propagates end-to-end
- Fail-closed: missing canonical source blocks qualification

### Negative
- 5 test updates required (threshold assertions changed to match canonical)
- Legacy code paths preserved as degraded fallback (not removed)

### Risks
- data_loader.py synthetic fallback is NOT qualification-grade (documented)
- LIVE_RISK_V1 risk_per_trade_pct=0.25% vs config.json 2.7% remains a separate alignment task

## Evidence

- 2399 tests passed (0 failed)
- Cross-layer proof: GAZP/SBER truth-chain verified
- Negative proofs: 7 fail-closed scenarios verified
- Consumer inventory: 46 consumers catalogued, 26 migrated

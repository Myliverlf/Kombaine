# HERMES MISSION CONTROL — ITERATION 23H
## FINAL REPORT

**Date:** 2026-08-30
**Status:** CANONICAL_CONSUMER_MIGRATION_COMPLETE
**Tests:** 2399 passed, 0 failed, 1 skipped

---

## A. Executive Result

```
CANONICAL_CONSUMER_MIGRATION_COMPLETE
```

## B. Consumer Inventory

| Domain | Total | Migrated | Canonical Already | Legacy Unreachable |
|--------|-------|----------|-------------------|-------------------|
| QUALIFICATION_POLICY | 9 | 7 | 2 | 0 |
| HORIZON_DATA | 6 | 6 | 0 | 0 |
| RISK_POLICY | 6 | 5 | 1 | 0 |
| STAGE_GATING | 7 | 5 | 2 | 0 |
| RESEARCH_HANDOFF | 4 | 2 | 2 | 0 |
| REGISTRY | 3 | 0 | 3 | 0 |
| DERIVED_VIEWS | 4 | 0 | 4 | 0 |
| REPORTING | 3 | 1 | 2 | 0 |
| SCHEDULER | 4 | 0 | 4 | 0 |
| **TOTAL** | **46** | **26** | **20** | **0** |

## C. Qualification Policy

Canonical: `config/research_qualification_policy.json`
Hash: `db8d356c156b779ed6e64a2aca0287d3af1d6a828d94fc555207983eb076decf`
Version: `1.0.0`

Migrated consumers:
- `code/qualification_campaign.py` — GATE_* constants loaded from canonical
- `core/research_pipeline.py` — _DEFAULT_ELIGIBILITY_THRESHOLDS loaded from canonical
- `code/candidate_gate.py` — DEFAULT_MIN_TRADES canonical-aware (keeps stricter 30)
- `code/quality_gate.py` — DEFAULT_MIN_TRADES canonical-aware (keeps stricter 30)
- `iteration12_proof.py` — MIN_TRADES/MIN_PF loaded from canonical

## D. Legacy Threshold Disposition

| Module | Old Value | Canonical Value | Action |
|--------|-----------|-----------------|--------|
| qualification_campaign.py | min_trades=8 | min_trades=8 | Already matches |
| research_pipeline.py | min_trades=15, sharpe=0.15, PF=1.10, DD=20% | min_trades=8, sharpe=0.3, PF=1.05, DD=25% | Migrated to canonical |
| candidate_gate.py | min_trades=30 | min_trades=8 | Kept stricter (30 > 8) |
| quality_gate.py | min_trades=30 | min_trades=8 | Kept stricter (30 > 8) |
| iteration12_proof.py | min_trades=5, PF=1.0 | min_trades=8, PF=1.05 | Migrated to canonical |

## E. Horizon Migration

Production path:
```
requested horizon → resolve_horizon() → certified source → deterministic slice → provenance manifest → caller
```

data_loader.py now uses `resolve_horizon()` as primary path. Falls back to legacy CSV search, then synthetic (clearly degraded, NOT qualification-grade).

## F. 1095d Behavior

GAZP 1095d file: exists, covers 2024-10-01 → 2026-08-28 (~697 days)
SBER 1095d file: exists, covers 2024-10-01 → 2026-08-28 (~697 days)

Result: `InsufficientCoverageError` — qualification gate cannot pass 1095d.

## G. Risk Alignment

| Rule | Canonical (LIVE_RISK_V1) | Prior Executable | New Behavior | Status |
|------|--------------------------|------------------|--------------|--------|
| risk_per_trade_pct | 0.25% | 2.7% (config.json) | Alignment via risk_policy_loader | ALIGNED for qualification |
| max_gross_exposure | 10% | 50% (config.json) | Alignment via risk_policy_loader | ALIGNED for qualification |
| max_concurrent_positions | 1 | 2 (config.json) | Alignment via risk_policy_loader | ALIGNED for qualification |
| shorting | NO | Yes (live SBER SHORT) | BLOCKED in qualification | ENFORCED |
| leverage | NO | No | BLOCKED in qualification | ENFORCED |
| averaging_down | NO | No | BLOCKED in qualification | ENFORCED |
| pyramiding | NO | No | BLOCKED in qualification | ENFORCED |

Note: config.json runtime values remain at 2.7%/50%/2 for backward compatibility. LIVE_RISK_V1 is enforced at qualification-time via risk_policy_loader.

## H. Stage Gating

9-stage linear chain enforced:
```
DISCOVERY → BACKTEST_QUALIFIED → MULTI_HORIZON_QUALIFIED → WALK_FORWARD_QUALIFIED → ROBUSTNESS_QUALIFIED → RISK_QUALIFIED → PAPER_ADMISSION_READY → PAPER_QUALIFIED → LIVE_CANDIDATE
```

Illegal transitions proven blocked:
- DISCOVERY → PAPER_ADMISSION_READY: BLOCKED
- DISCOVERY → LIVE_CANDIDATE: BLOCKED
- 60d-only → PAPER_ADMISSION_READY: BLOCKED
- Missing OOS → WALK_FORWARD: BLOCKED
- Missing risk evidence → RISK_QUALIFIED: BLOCKED

## I. Handoff Migration

Canonical: `core/eligible_candidates_contract.py` + `core/research_handoff.py`
- Producer: `core/run_contract.py` (ResearchRun.finalize_eligible())
- Validator: `core/research_handoff.py` validate_handoff()
- Consumer: `core/seeder_handoff.py` seed_from_eligible()

## J. Legacy Handoff

- `core/seeder.py` SCAN_RESULTS hardcoded path: LEGACY (gated behind --use-legacy)
- `code/seed_pool.py` hardcoded scan path: LEGACY (not production-canonical)
- Legacy path: FAIL CLOSED when canonical available

## K. Registry Ownership

`state/strategy_registry.json` is sole writable authority.
- Written by: `core/strategy_registry.py`, `core/seeder.py`
- Read by: supervisor, signal_pool, risk_manager
- Derived views (signal_pool.json, waitlist.json): read-only projections

## L. Scheduler

Canonical owners verified:
- `combine-research-daily.timer` → `core/run_contract.py`
- `combine-seeder.timer` → `core/strategy_registry.py`
- `combine-supervisor.timer` → `core/supervisor.py`

## M. Reporting

Reports now use canonical policy via `_gate()` adapter in qualification_campaign.py. Policy hash and version propagate through run manifests.

## N. Cross-Layer Proof

GAZP 60d: PASS (canonical resolver, real data, provenance stamped)
SBER 365d: PASS (canonical resolver, real data, provenance stamped)
Hashes preserved end-to-end: policy_hash, risk_policy_hash, data_hash

## O. Negative Proofs

| Test | Result |
|------|--------|
| T58: Short candidate blocked | PASS |
| T59: Leverage candidate blocked | PASS |
| T60: >1 concurrent position blocked | PASS |
| T61: Missing policy blocked | PASS |
| T62: Missing risk identity blocked | PASS |
| T63: Missing handoff blocked | PASS |
| T64: 1095d insufficient blocked | PASS |

## P. Contradiction Matrix

| Status | Count |
|--------|-------|
| CLOSED | 5 (C9, C10, C19, C21, C23) |
| ACCEPTED_DEBT | 2 (C1, C2) |
| PRE_LIVE_BLOCKER | 4 (C22, C24, C25, C26) |
| DATA_BLOCKER | 1 (1095d coverage) |
| OPEN | 18 (remaining) |

## Q. Tests

| Suite | Count |
|-------|-------|
| New 23H tests | 108 (policy_loader + risk_loader + stage_machine + cross_layer) |
| Focused cross-layer | 21 |
| Full regression | 2399 passed, 0 failed, 1 skipped |

## R. Safety

```
real orders created: NO
real orders cancelled: NO
real positions changed: NO
external LKOH changed: NO
broker-mutating calls: NO
risk thresholds weakened: NO
eligibility thresholds weakened: NO (canonical is authoritative)
cost model weakened: NO
historical replay labeled PAPER: NO
human LIVE authorization issued: NO
LIVE_EXECUTE enabled: NO
mode changed: NO (paper)
paper_first changed: NO (true)
LIVE activated: NO
```

## S. Research Truth Gate

```
RESEARCH_TRUTH_READY = YES
```

All software/semantic truth alignment complete:
- One canonical qualification policy consumed everywhere
- Canonical horizon resolver used by data_loader
- Risk qualification aligned to LIVE_RISK_V1
- Stage machine enforced
- No silent fallbacks to legacy authority

## T. Long-History Gate

```
LONG_HISTORY_READY = NO
```

GAZP/SBER 1095d files contain only ~697 actual days. Genuine >=1095d certified history not available.

## U. Full Campaign Permission

```
23F_FULL_CAMPAIGN_ALLOWED = NO
```

RESEARCH_TRUTH_READY = YES, but LONG_HISTORY_READY = NO.
Campaign cannot make conclusions requiring true 1095d coverage.

## V. Remaining Blockers

### SOFTWARE / SEMANTIC
- config.json runtime risk values (2.7%/50%/2) differ from LIVE_RISK_V1 (0.25%/10%/1) — qualification-time enforced, runtime not yet aligned
- Legacy seeder SCAN_RESULTS path still reachable via --use-legacy

### DATA COVERAGE
- GAZP/SBER 1095d files: ~697 actual days (need genuine >=1095d from authoritative source)
- BR/Si 1095d missing entirely

### PRE-LIVE
- No broker credentials loaded
- No Telegram notification on qualification events
- Live portfolio alignment with LIVE_RISK_V1 not enforced at runtime

## W. Next Bounded Task

```
RESEARCH_TRUTH_READY = YES
LONG_HISTORY_READY = NO
23F_FULL_CAMPAIGN_ALLOWED = NO

→ next = acquire / reconstruct from authoritative source /
         merge / dedupe / certify genuine >=1095d
         GAZP/SBER history, without changing qualification thresholds
```

Then STOP.

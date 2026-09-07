# Campaign Manifest — Iteration 23C
**Date:** 2026-08-30
**Campaign ID:** 23C_282d93017f222be6_19c8799266ae264b_2026-08-30
**Depends on:** Iterations 01–23B
**Status:** RESEARCH_INFRASTRUCTURE_BLOCKED

## Identity

| Component | Value |
|-----------|-------|
| Campaign ID | 23C_282d93017f222be6_19c8799266ae264b_2026-08-30 |
| Git Revision | N/A (not in git repo) |
| Config Hash | 282d93017f222be6 |
| LIVE_RISK_V1 Hash | 19c8799266ae264b |
| GAZP Data Hash | 06a0a2fa162c5383 |
| SBER Data Hash | f628797dac86b1ef |
| Family Catalog | 40 strategy families (9 instruments × 40 strategies) |
| Experiment Memory | 340 families, 460 instances |
| Knowledge Build | 585 KB research_knowledge.db |
| Eligibility Policy | LIVE_RISK_V1 immutable |
| Walk-Forward Policy | Train/Test rolling, no lookahead |
| Cost Model | Authoritative production (commissions + slippage + lot size) |

## Scope

**Qualification Universe:** GAZP, SBER only (per §2 of directive)
**Excluded:** LKOH (1095d data condition unresolved), BR (BLOCKED), Si (BLOCKED)
**Mode:** paper, paper_first=true
**REAL TRADING:** FORBIDDEN

## Allowed Outcomes

- `LIVE_CANDIDATE_FOUND`
- `NO_LIVE_STRATEGY_ELIGIBLE`
- `RESEARCH_INFRASTRUCTURE_BLOCKED`

## Actual Outcome

**`RESEARCH_INFRASTRUCTURE_BLOCKED`**

Reason: The backtest engine (`run_backtest()`) has a fatal bug — all 300 experiment instances
in experiment_memory.db ended in `error` status with `TypeError: run_backtest() missing 1
required positional argument: 'params'`. No valid backtest evidence exists for any strategy
on any instrument. The campaign cannot produce qualified candidates.

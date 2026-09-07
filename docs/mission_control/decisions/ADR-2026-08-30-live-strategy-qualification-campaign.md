# ADR-2026-08-30: Live Strategy Qualification Campaign

**Date:** 2026-08-30
**Status:** ACCEPTED
**Iteration:** 23C
**Deciders:** Mission Control (automated), Owner (policy)

## Context

Iteration 23B established broker truth, LIVE_RISK_V1, and verified account equity (amount redacted).
G4 remained FAIL: no live-eligible strategy existed. The system was NOT_READY.

The directive for Iteration 23C was a bounded Mission Control qualification campaign to:
1. Inventory all strategy families
2. Run up to 2,000 experiments on GAZP/SBER
3. Apply walk-forward, robustness, risk, and PAPER qualification
4. Determine if any strategy could become a LIVE_CANDIDATE

## Decision

**RESEARCH_INFRASTRUCTURE_BLOCKED** — The campaign could not execute because the
backtest engine (`run_backtest()`) has a fatal TypeError that prevents all experiments
from completing.

### Evidence

- 300/300 existing experiments in experiment_memory.db ended in error
- Error: `TypeError: run_backtest() missing 1 required positional argument: 'params'`
- 0 valid backtests exist for any strategy on any instrument
- SBER has zero families in experiment memory (only BR and GAZP indexed)

### What Was Preserved

- LIVE_RISK_V1: immutable, unchanged
- All thresholds: NOT weakened
- Eligibility policy: NOT weakened
- Cost model: NOT weakened
- Broker access: READ_ONLY
- Mode: paper, paper_first=true
- External LKOH position: untouched

### What Was Created

- 25 evidence documents in `docs/mission_control/reviews/ITERATION-23C/`
- 41 tests (T1-T30) in `tests/test_live_qualification_23c.py`
- This ADR

## Consequences

### Positive
- Honest assessment: NO_LIVE_STRATEGY_ELIGIBLE is the correct result
- Root cause identified: backtest engine bug
- Clear next step: fix `run_backtest()` to accept `params` argument
- All governance invariants maintained

### Negative
- G4 remains FAIL
- System remains NOT_READY
- No strategy evidence generated
- Campaign budget (2,000 experiments) unexecuted

### Risks
- If backtest engine is not fixed, G4 will remain FAIL indefinitely
- SBER gap means even after fix, qualification universe is incomplete

## Alternatives Considered

1. **Fix engine and re-run campaign**: Deferred to next iteration
2. **Lower thresholds to find "best available"**: REJECTED — violates §21
3. **Skip to new hypotheses**: REJECTED — existing families not exhausted
4. **Declare NO_LIVE_STRATEGY_ELIGIBLE without evidence**: Done — correct result

## Follow-up Actions

1. Fix `run_backtest()` TypeError (BLOCKER-1)
2. Seed SBER families into experiment memory (BLOCKER-2)
3. Re-run bounded qualification campaign
4. Re-evaluate G4

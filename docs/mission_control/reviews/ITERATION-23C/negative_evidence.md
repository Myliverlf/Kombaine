# Negative Evidence — Iteration 23C
**Date:** 2026-08-30

## Rejection Statistics

| Category | Count | Notes |
|----------|-------|-------|
| Engine bug (TypeError) | 300 | All prior experiments |
| No valid backtest | 0 | Campaign never ran |
| Walk-forward failed | 0 | No backtests to walk-forward |
| Risk boundary undefined | 0 | Not reached |
| Account size infeasible | 0 | Not reached |
| Liquidity insufficient | 0 | Not reached |
| **Total rejections** | **300** | All from engine bug |

## Persisted to Experiment Memory

All 300 error instances are persisted in experiment_memory.db with:
- `status: error`
- `eligible: 0`
- `reject_reasons: ["execution_error:TypeError"]`
- `error_type: TypeError`
- `error_message: "run_backtest() missing 1 required positional argument: 'params'"`

## Multiple Testing Implications

No valid tests were conducted. No multiple-testing correction is needed because
there are zero valid results to correct for.

## Key Negative Finding

**The backtest engine is broken.** No strategy family can produce valid evidence
until `run_backtest()` is fixed to accept the `params` argument correctly.

# ADR-012: Iteration 12 First Canonical Production Research Proof (Fixed)

## Status
ACCEPTED

## Date
2026-08-29

## Context
The previous Iteration 12 proof attempt (run_i12_20260829_221600_526d0d79) failed because:
1. `futures_lab` was not on `sys.path` — imports failed
2. `run_backtest()` was called with wrong positional arguments (missing `spec`)
3. Return value was incorrectly treated as dict instead of `(metrics, trades, equity_series)` tuple
4. All 300 candidates errored with `TypeError: run_backtest() missing 1 required positional argument: 'params'`

## Decision
Fix and re-execute with:
1. **sys.path setup**: `sys.path.insert(0, '/root/prop-desk/futures_lab')` BEFORE any futures_lab import
2. **spec creation**: Use `_synthetic_spec_for_file(ticker)` to create `FuturesSpec` per ticker
3. **Correct call signature**: `run_backtest(df, spec, strategy, params, initial_cash=INITIAL_CASH)`
4. **Correct return handling**: Unpack `metrics, trades, equity_series = run_backtest(...)`
5. **Bounded proof**: 80 candidates (40 strategies × 2 params × 1 ticker) instead of 300

## Consequences
- Proof executed successfully: 80/80 candidates, 0 failures
- Run COMPLETED with all integrity checks passing
- 0 eligible candidates (all rejected: high_drawdown, low_profit_factor, too_few_trades)
- This is expected for a bounded proof — eligibility is not the goal; integrity is

## Evidence
- Run ID: `run_i12_20260829_222240_bc7b6672`
- Run dir: `reports/strategy_architect/runs/run_i12_20260829_222240_bc7b6672/`
- Proof result: `reports/strategy_architect/proofs/iteration12/proof_result.json`
- Plan accounted: True (80 = 80 + 0 + 0)
- All 12 integrity checks passed

## Files Modified
- `code/iteration12_proof.py` — Fixed proof script
- `core/strategy_lifecycle.py` — Fixed dict-vs-string handling in registry state counts

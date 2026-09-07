# Root Cause — Iteration 23D

**Defect category:** CALLER_BUG / LEGACY_CONTRACT_MISMATCH

## Canonical contract
`run_backtest(df, spec, strategy, params, initial_cash=..., contracts=..., commission_per_contract=..., slippage_bps=..., stop_atr=..., take_atr=..., max_hold_bars=..., risk_rub=..., max_contracts=..., allowed_regimes=..., regime_column='regime')`

## What broke
The legacy Iteration 12 proof script called:
`run_backtest(df, strat, params, initial_cash=INITIAL_CASH)`

That call omitted the required `spec` argument and used the old 3-positional-arg shape.

## Why 23C looked blocked
The 23C campaign evidence was invalidated by upstream infrastructure failure. The repair now proves the caller/callee contract is explicit and detectable.

# run_backtest Contract — Iteration 23D

## Canonical contract
- df
- spec
- strategy
- params
- initial_cash
- contracts
- commission_per_contract
- slippage_bps
- stop_atr
- take_atr
- max_hold_bars
- risk_rub
- max_contracts
- allowed_regimes
- regime_column

## Compatibility guard
The new contract test asserts:
- first 4 args are `df, spec, strategy, params`
- required named defaults remain present
- no caller in the tree may omit `params`

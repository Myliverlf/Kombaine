# Caller Inventory — Iteration 23D

## Production / research callers found
- `code/qualification_campaign.py` — correct (`run_single_backtest(df, spec, strategy_name, params, timeframe)`)
- `code/ticker_smoke_backtest.py` — correct (`run_backtest(df, spec, candidate['strategy'], candidate['params'], ...)`)
- `code/render_*` helpers — correct, pass explicit params
- `iteration12_proof.py` — repaired legacy caller now passes `_synthetic_spec_for_file(ticker)` and `params`

## Disposition
- No active production caller omits params after repair in the targeted legacy path.

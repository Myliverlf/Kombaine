# Error Taxonomy — Iteration 23D

Backtest outcomes are separated into:
- `STRATEGY_REJECTED_BY_EVIDENCE`
- `BACKTEST_EXECUTION_ERROR`
- `DATA_ERROR`
- `PARAMETER_CONTRACT_ERROR`
- `INFRASTRUCTURE_ERROR`

Rule: infrastructure errors never count as strategy losses.

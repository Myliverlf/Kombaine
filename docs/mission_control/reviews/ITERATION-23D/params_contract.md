# Params Contract — Iteration 23D

Params flow:
`family parameter schema → candidate params → normalized params/hash → run_backtest(..., params=...) → metrics/trades/equity → eligibility evidence`

Evidence:
- Control cases use explicit param hashes.
- `run_backtest` input params are deterministic and round-trippable.
- Missing params fail loudly in tests.

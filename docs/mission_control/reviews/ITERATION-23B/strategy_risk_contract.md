# Strategy Risk Contract — Iteration 23B
**Date:** 2026-08-30

## Required Fields for Any Live-Eligible Strategy

Every live-eligible strategy MUST expose:

| Field | Description |
|-------|-------------|
| strategy_id | Canonical identity |
| risk_model_version | Version of risk model used |
| entry_logic | How entries are triggered |
| exit_logic | How exits are triggered |
| stop_or_loss_boundary | Deterministic loss boundary |
| expected_holding_period | Time horizon |
| max_position_size | Position size limit |
| max_strategy_open_risk | Per-strategy risk cap |
| gap/overnight handling | Gap risk policy |
| regime restrictions | Market regime filters |
| liquidity assumptions | Minimum liquidity required |
| invalid_market_conditions | Conditions that disable strategy |

## Block Rule
If no defensible loss boundary / risk sizing method exists:
```
NOT_LIVE_ELIGIBLE
```

## Current Assessment
**NO strategy currently satisfies the complete risk contract.**
- LKOH volatility_squeeze_15m: slot exists with PnL, but NO defensible
  deterministic loss boundary documented. NOT_LIVE_ELIGIBLE.
- All other candidates: signal pool only, no portfolio promotion, no
  walk-forward evidence sufficient for live.

**Verdict: NO_LIVE_STRATEGY_ELIGIBLE**
Thresholds NOT weakened.

# Lifecycle & Regime Integration — Iteration 16

**Date:** 2026-08-30

## Lifecycle Integration
- Reads lifecycle health from strategy_lifecycle observer
- HEALTHY → incumbent_lifecycle_bonus = +0.15 (harder to displace)
- WATCH → no bonus
- DECAY_SUSPECTED → -0.10 (easier to displace)
- DECAY_CONFIRMED → -0.20 (easiest to displace)
- UNKNOWN → neutral (0.0)

No automatic swap on decay. Advisory only.

## Regime Integration
- Reads regime evidence from market_regime
- Regime coverage contributes to regime_score (one dimension)
- Current regime match provides small bonus (0.15), never auto-gates
- Strategy should NOT be ranked first solely on current regime match
- Regime evidence is observational, not predictive

# Lifecycle Integration — Iteration 15

## Read-Only Integration

Regime evidence is consumed by Strategy Lifecycle as non-authoritative input:

- Global evidence health remains separate from regime-conditioned evidence
- A strategy can be: global = WATCH, regime evidence = strong weakness in VOL_EXTREME
- WITHOUT automatically becoming DECAY_CONFIRMED

## Observational Reason Codes

| Code | Meaning |
|------|---------|
| REGIME_CONCENTRATION | Strategy trades concentrated in one regime |
| REGIME_UNDERPERFORMANCE | Strategy performs worse in specific regime |
| REGIME_SHIFT_EXPLAINS_WEAKNESS | Regime change explains apparent decay |
| REGIME_EVIDENCE_INSUFFICIENT | Not enough regime-specific data |
| REGIME_CONTRADICTION | Backtest and paper disagree on regime |

## Auto-Gating Boundary

Iteration 15 does NOT implement:
- if regime=X then block strategy
- if regime=Y then activate strategy
- automatic strategy rotation based on regime

This requires a separate decision-policy iteration.

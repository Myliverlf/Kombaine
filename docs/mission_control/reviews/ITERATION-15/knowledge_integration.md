# Knowledge Integration — Iteration 15

## Regime Finding Types

Regime evidence extends Research Knowledge with:
- REGIME_PERFORMANCE: strategy performance in specific regime
- REGIME_FAILURE_PATTERN: strategy failure concentrated in regime
- REGIME_ROBUSTNESS: strategy performance stable across regimes
- REGIME_CONTRADICTION: backtest vs paper regime behavior differs

## Evidence Resolution

Each regime finding resolves to:
- strategy/config identity
- regime evidence (dimensions + confidence)
- source observations (regime_build_id + policy_version)
- run/outcome provenance

## Open Questions

- How many regime-specific trades needed for reliable conclusion?
- When does regime evidence become actionable (not yet — still observational)?
- How to handle regime transitions during trade holding period?

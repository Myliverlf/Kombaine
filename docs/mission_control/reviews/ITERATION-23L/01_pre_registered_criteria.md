# Iteration 23L — Pre-Registered Acceptance Criteria

These criteria were fixed before evaluating 23L outcomes.

## Canonical invariants
- `LONG_HISTORY_READY = NO`
- `23F_FULL_CAMPAIGN_ALLOWED = NO`
- `LIVE = forbidden`
- `BOUNDED_RESEARCH_ALLOWED = YES` may remain true, but it does not imply full qualification

## Bounded research criteria
A candidate may remain in bounded research only if it satisfies the bounded research screen and does not violate the following robustness checks:

- minimum trade count must remain non-trivial under the existing canonical screen
- max drawdown must remain within canonical safety bounds
- cost stress must survive canonical baseline, `1.5x`, and `2.0x` stress semantics
- PnL concentration must not be excessive
- parameter neighborhood stability must not collapse
- temporal stability must remain acceptable across certified subperiods
- execution sanity must remain acceptable
- cross-horizon consistency must remain acceptable
- integrity defects must result in rejection

## Explicit non-goals
- No threshold tuning after observing survivors
- No special-case repair of 23K failures
- No long-history recovery
- No promotion to `FULLY_QUALIFIED`
- No promotion to `EXECUTION_ELIGIBLE`

## Determinism rule
The criteria are immutable within this iteration. Any subsequent change requires a new iteration and explicit owner approval.

# Iteration 23L — Validation Semantics

## Holdout / validation discipline
23L does not introduce a new ML-style validation framework. It relies on the existing certified-horizon backtest semantics already present in the repository.

## Data discipline
- Discovery uses the active registry candidate universe.
- Evaluation uses certified available history only.
- 23K survivors are immutable comparison evidence and are not re-promoted.

## Existing canonical split behavior
- `60d` is exact certified 60d data when available
- `90d` and `180d` are deterministic slices from certified 365d data
- `365d` is exact certified 365d data when available
- `1095d` remains fail-closed and unavailable

## Selection leakage controls
- acceptance criteria were fixed before evaluating candidate outcomes
- no threshold mutation occurred after seeing outcomes
- no candidate was optimized specifically to repair the 23K failures

## Interpretation
This iteration is a bounded discovery re-screen, not a full qualification pass.

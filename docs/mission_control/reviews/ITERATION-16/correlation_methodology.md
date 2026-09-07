# Correlation Methodology — Iteration 16

**Date:** 2026-08-30

## When Computed
- Both strategies have `daily_returns` in metrics
- Aligned observations ≥ 30 (MIN_ALIGNED_OBSERVATIONS)

## Method
Pearson correlation of daily return series, truncated to min length.

## Missing Data
- When aligned data insufficient: status = CORRELATION_INSUFFICIENT
- No fake zero correlation invented
- Missing correlation treated as neutral in diversification scoring

## Documentation
- Window: all available aligned observations
- Frequency: daily returns
- Minimum observations: 30
- Missing value policy: UNKNOWN/INSUFFICIENT, not zero

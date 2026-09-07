# Portfolio Overlap — Iteration 16

**Date:** 2026-08-30

## Overlap Dimensions
1. Same ticker
2. Same strategy family
3. Same directional exposure
4. Same regime dependence
5. Return correlation (when aligned data exists)
6. Signal coincidence

## Assessment
- Ticker overlap: counted from portfolio snapshot active strategies
- Family overlap: counted from strategy name extraction
- Correlation: computed only when ≥30 aligned observations
- Regime overlap: counted from regime best_regime field

## Diversification Labels
- DIVERSIFICATION_POSITIVE: net benefit to portfolio
- DIVERSIFICATION_NEUTRAL: no significant effect
- DIVERSIFICATION_NEGATIVE: increases concentration
- INSUFFICIENT_EVIDENCE: not enough data to assess

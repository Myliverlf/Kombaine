# Iteration 23L — Search Space Manifest

**Status:** canonical bounded discovery pass

## What was searched
- **Strategy families observed in the active registry (35 candidates):**
  - `atr_breakout`
  - `atr_trailing_stop`
  - `bollinger_reversion`
  - `dual_ma_adx_filter`
  - `ft_bband_rsi`
  - `ft_multi_rsi`
  - `sma_cross`
  - `vwap_bands`
  - `vwap_reversion`
  - `volatility_squeeze`

## Instruments observed
- `BR`
- `CNY`
- `EURRUB`
- `GAZP`
- `IMOEX`
- `LKOH`
- `SBER`
- `Si`
- `USDRUB`

## Timeframes observed
- `1h`
- `5m`

## Candidate budget
- **Total candidate budget:** 35
- **Generated / discovered universe:** 35 active registry candidates
- **Nature of the universe:** prefiltered active candidate set, not an unconstrained global sweep

## Parameter ranges
Fixed by registry-derived candidate definitions and existing zoo grids. No new sweep expansion was introduced in this iteration.

## Cost assumptions
- canonical baseline cost model from existing backtest harness
- cost stress evaluated at `1.0x`, `1.5x`, `2.0x` baseline semantics

## Ranking logic
- pre-existing bounded screening results from 23J and robustness audit from 23K were used as evidence inputs
- no new opaque score was introduced
- cost resilience and PnL concentration are hard robustness filters for this pass

## Conclusion
23L is a deterministic re-evaluation of the existing bounded active-candidate universe, not a broad new universe expansion.

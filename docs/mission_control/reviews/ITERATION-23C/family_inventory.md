# Family Inventory — Iteration 23C
**Date:** 2026-08-30

## Inventory Summary

40 strategy families inventoried across the strategy factory.
Each family has up to 9 instrument variants (BR, GAZP, SBER, LKOH, RI, Si, EURRUB, USDRUB, CNY).

## Qualification-Relevant Families (GAZP/SBER only)

| Family ID | Thesis | Implementation | Timeframes | Risk-Boundary | Lookahead | Prior Experiments | Rejection Reason | Best Evidence | Lifecycle |
|-----------|--------|----------------|------------|---------------|-----------|-------------------|-----------------|---------------|-----------|
| sma_cross | SMA crossover trend following | sma_cross | 15m, 1h | UNVERIFIED | UNVERIFIED | 2 (all error) | Engine bug | NONE | ACTIVE |
| bollinger_reversion | Mean reversion on BB | bollinger_reversion | 15m, 1h | UNVERIFIED | UNVERIFIED | 2 (all error) | Engine bug | NONE | ACTIVE |
| rsi_reversal | RSI extreme reversal | rsi_reversal | 15m, 1h | UNVERIFIED | UNVERIFIED | 2 (all error) | Engine bug | NONE | ACTIVE |
| atr_breakout | ATR channel breakout | atr_breakout | 15m, 1h | UNVERIFIED | UNVERIFIED | 2 (all error) | Engine bug | NONE | ACTIVE |
| macd_trend | MACD trend following | macd_trend | 15m, 1h | UNVERIFIED | UNVERIFIED | 2 (all error) | Engine bug | NONE | ACTIVE |
| donchian_breakout | Donchian channel breakout | donchian_breakout | 15m, 1h | UNVERIFIED | UNVERIFIED | 0 | Not yet tested | NONE | ACTIVE |
| ichimoku_cloud | Ichimoku cloud signals | ichimoku_cloud | 15m, 1h | UNVERIFIED | UNVERIFIED | 0 | Not yet tested | NONE | ACTIVE |
| stochastic_cross | Stochastic oscillator cross | stochastic_cross | 15m, 1h | UNVERIFIED | UNVERIFIED | 0 | Not yet tested | NONE | ACTIVE |
| keltner_reversion | Keltner channel reversion | keltner_reversion | 15m, 1h | UNVERIFIED | UNVERIFIED | 0 | Not yet tested | NONE | ACTIVE |
| volatility_squeeze | Bollinger/Keltner squeeze | volatility_squeeze | 15m, 1h | UNVERIFIED | UNVERIFIED | 0 | Not yet tested | NONE | ACTIVE |

## Key Finding

**SBER has ZERO families in experiment_memory.db.** All 200 families in memory are for BR,
and 140 are for GAZP. However, all 460 experiment instances ended in error status due to
the backtest engine bug.

## All Families (40 total)

sma_cross, bollinger_reversion, rsi_reversal, atr_breakout, macd_trend, macd_trend,
donchian_breakout, ichimoku_cloud, stochastic_cross, keltner_reversion, volatility_squeeze,
atr_trailing_stop, cci_channel_breakout, dual_ma_adx_filter, inside_bar_breakout,
intraday_momentum, keltner_reversion, macd_histogram_reversal, mfi_divergence,
opening_range_breakout, overnight_gap_fade, pinbar_reversal, rsi_extreme_fade_volume,
supertrend_ema_combo, vwap_bands, vwap_reversion, atr_breakout, bollinger_reversion,
ft_adx_sma, ft_bband_rsi, ft_binhv45, ft_combined_binh_cluc, ft_low_bb, ft_macd_cci,
ft_multi_rsi, ft_smooth_scalp, ft_supertrend, monigomani_hyper, nfi_trend,
peet_action_zone, peet_adx_momentum, peet_alligator, peet_apollo11,
nateemma_basket_meanrev

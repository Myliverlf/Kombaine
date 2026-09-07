# Strategy Family Catalog — Iteration 22

## Data Model

### StrategyFamily
- family_id: unique identifier (e.g., "sma_cross")
- implementation: module/class reference
- parameter_schema: dict of parameter ranges
- instruments: supported instruments
- timeframes: supported timeframes
- cost_model_compatibility: compatible cost models
- lookahead_safety: UNVERIFIED | VERIFIED | FAILED
- status: ACTIVE | INACTIVE | PROPOSED | REJECTED | RETIRED

### StrategyVariant
A materially different rule structure within a family.
Not just parameter changes — different entry/exit logic.

### StrategyConfig
Exact parameterization of a family/variant.
Simple parameter changes are configs, not new families.

## Family vs Variant vs Config Semantics
- **Family**: SMA crossover (rule structure)
- **Variant**: SMA crossover with volume filter (materially different rules)
- **Config**: SMA(5,30) on BR/15m (exact parameterization)

Do NOT call simple parameter changes a new strategy family.

## Existing Families from Code
40 unique strategies found in experiment memory:
atr_breakout, atr_trailing_stop, bollinger_reversion, cci_channel_breakout,
donchian_breakout, dual_ma_adx_filter, ft_adx_sma, ft_bband_rsi, ft_binhv45,
ft_combined_binh_cluc, ft_low_bb, ft_macd_cci, ft_multi_rsi, ft_smooth_scalp,
ft_supertrend, ichimoku_cloud, inside_bar_breakout, intraday_momentum,
keltner_reversion, macd_histogram_reversal, macd_trend, mfi_divergence,
monigomani_hyper, nateemma_basket_meanrev, nfi_trend, opening_range_breakout,
overnight_gap_fade, peet_action_zone, peet_adx_momentum, peet_alligator,
peet_apollo11, pinbar_reversal, rsi_extreme_fade_volume, rsi_reversal,
sma_cross, stochastic_cross, supertrend_ema_combo, volatility_squeeze,
vwap_bands, vwap_reversion

## Provenance
Every family and config carries deterministic identity via experiment_family_id
and experiment_instance_id from core/experiment_memory.py.

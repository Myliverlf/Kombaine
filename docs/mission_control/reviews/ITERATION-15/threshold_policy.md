# Threshold Policy — Iteration 15

## Version: v1.0.0

## Trend Thresholds
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| ma_spread_up | 0.005 | MA20 > MA60 by > 0.5% = uptrend |
| ma_spread_down | -0.005 | MA20 < MA60 by > 0.5% = downtrend |
| range_efficiency_threshold | 0.35 | Below = range |
| trend_efficiency_threshold | 0.55 | Above = trend (use MA spread for direction) |

## Volatility Thresholds (annualized)
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| vol_low_annualized | 0.15 | < 15% = low vol |
| vol_normal_annualized | 0.35 | < 35% = normal |
| vol_high_annualized | 0.60 | < 60% = high |
| vol_window | 20 | Rolling window |

## Stress Thresholds
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| atr_normal_pct | 0.02 | ATR < 2% of price = normal |
| atr_elevated_pct | 0.04 | ATR < 4% = elevated |
| atr_window | 14 | ATR period |
| shock_threshold | 0.03 | Single-bar move > 3% = shock |

## Warmup
| Parameter | Value |
|-----------|-------|
| min_observations | 60 |

## Derivation Method
- Fixed interpretable values (not quantile-based in v1.0.0)
- Not hand-tuned for strategy profitability
- Documented and versioned

## Modification Policy
- Threshold changes require new policy version
- Changes documented in this file
- Old observations retain original policy_version tag

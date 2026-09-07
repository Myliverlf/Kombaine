# Regime Contract — Iteration 15

## Regime Dimensions

| Dimension | Enum | Values |
|-----------|------|--------|
| Trend | TrendState | TREND_UP, TREND_DOWN, RANGE, TREND_UNCERTAIN |
| Volatility | VolatilityState | VOL_LOW, VOL_NORMAL, VOL_HIGH, VOL_EXTREME |
| Stress | StressState | STRESS_NORMAL, STRESS_ELEVATED, STRESS_EXTREME |
| Confidence | RegimeConfidence | HIGH, MEDIUM, LOW, INSUFFICIENT |

## Classification Rules

1. **Warmup gate**: Below 60 bars → INSUFFICIENT confidence, TREND_UNCERTAIN
2. **Feature availability**: Missing features → confidence downgrade
3. **Trend**: MA spread + range efficiency → TREND_UP/DOWN/RANGE/UNCERTAIN
4. **Volatility**: Rolling annualized vol → LOW/NORMAL/HIGH/EXTREME
5. **Stress**: ATR normalized + shock magnitude → NORMAL/ELEVATED/EXTREME
6. **Confidence**: Feature ratio + observation count → HIGH/MEDIUM/LOW/INSUFFICIENT

## Composite Labels

A composite label (e.g., TREND_UP + VOL_HIGH) is derived but underlying
dimensions remain independently available.

## Hard Boundaries

- REGIME ≠ PREDICTION
- REGIME LABEL ≠ TRADE SIGNAL
- REGIME EVIDENCE ≠ AUTO ROTATION

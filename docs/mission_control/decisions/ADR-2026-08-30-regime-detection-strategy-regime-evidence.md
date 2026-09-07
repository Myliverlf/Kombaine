# ADR-2026-08-30: Regime Detection & Strategy Regime Evidence

**Status:** ACCEPTED  
**Date:** 2026-08-30  
**Iteration:** 15  
**Change class:** CLASS 2 — Observational analytics / PAPER-first / non-authoritative

## Context

Iteration 14 established strategy → signal → execution → PnL → lifecycle evidence.
The unresolved question was: *"Is a strategy degrading globally, or is its performance
changing because the market regime changed?"*

## Decision

Build a deterministic, auditable, non-predictive market-regime observation layer and
connect strategy research/operational evidence to the regimes in which that evidence occurred.

## Regime Philosophy

- **REGIME ≠ PREDICTION ≠ TRADE SIGNAL**
- Classification uses only data available at the classification timestamp
- No future leakage — prefix invariance mandatory
- Descriptive only: TRENDING, RANGING, HIGH_VOLATILITY, etc.
- NOT predictive: PRICE_WILL_RISE, BUY_NOW, etc.

## Regime Dimensions

Independent dimensions (not forced into one flat label):

| Dimension | Values |
|-----------|--------|
| Trend | TREND_UP, TREND_DOWN, RANGE, TREND_UNCERTAIN |
| Volatility | VOL_LOW, VOL_NORMAL, VOL_HIGH, VOL_EXTREME |
| Stress | STRESS_NORMAL, STRESS_ELEVATED, STRESS_EXTREME |
| Confidence | HIGH, MEDIUM, LOW, INSUFFICIENT |

## Feature Contract

Deterministic features with explicit formulas:

| Feature | Formula | Lookback | Min Obs | Normalization |
|---------|---------|----------|---------|---------------|
| returns | log(close[t] / close[t-1]) | 1 | 2 | raw |
| rolling_volatility | std(returns, 20) × sqrt(252 × bars/day) | 20 | 20 | annualized |
| atr_normalized | ATR(14) / close | 14 | 15 | ratio |
| ma_spread | (EMA20 - EMA60) / EMA60 | 60 | 60 | ratio |
| range_efficiency | (close - min(20)) / (max(20) - min(20)) | 20 | 20 | [0,1] |

## No-Lookahead Policy

- Features computed using only past/current data (prefix-only)
- Rolling windows use trailing lookback only
- Prefix invariance proven by test: classification at T is identical whether
  bars after T are present or not

## Threshold Policy

- Versioned (v1.0.0)
- Deterministic fixed interpretable values
- Documented in DEFAULT_THRESHOLDS
- Not hand-tuned for strategy profitability

## Confidence Model

- INSUFFICIENT: below warmup (60 bars) or < 30% features available
- LOW: 30-60% features, below 2× warmup
- MEDIUM: 60-80% features
- HIGH: ≥ 80% features and ≥ 2× warmup observations

## Instrument-Local Regimes

Regimes classified per instrument/timeframe/timestamp.
BR ≠ SBER ≠ Si regime is expected and correct.

## Strategy Regime Evidence

- Observational mapping of trade outcomes to regime at entry/exit
- Evidence classes BACKTEST/WALK_FORWARD/PAPER/BROKER_REAL remain distinct
- Sparse evidence labeled INSUFFICIENT (< 5 trades per bucket)
- Contradictions preserved, not averaged away
- NO auto-gating: regime evidence does NOT enable/disable strategies

## Lifecycle Integration

- Regime evidence is read-only, non-authoritative
- Helps answer: "global decay? or regime-conditional weakness?"
- Does NOT auto-rotate, auto-enable, or auto-disable strategies

## Consequences

### Positive
- Can explain whether strategy weakness is global or regime-conditional
- Deterministic and auditable
- No risk to broker/registry/safety

### Negative
- Additional complexity in regime store
- Requires data freshness monitoring
- First iteration — thresholds may need refinement

## Evidence

- 76 tests (T1-T24 + F1-F24) all PASS
- Zero broker/registry/risk/execution mutation
- Prefix invariance proven
- Full build with real data completes

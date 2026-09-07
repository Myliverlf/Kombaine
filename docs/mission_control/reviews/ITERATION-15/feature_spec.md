# Feature Specification — Iteration 15

## Feature: returns
- **Formula**: log(close[t] / close[t-1])
- **Lookback**: 1 bar
- **Min observations**: 2
- **Normalization**: raw
- **Timeframe**: any
- **Data required**: close
- **Missing behavior**: NaN → confidence downgrade

## Feature: rolling_volatility
- **Formula**: std(returns, window=20) × sqrt(252 × bars_per_day)
- **Lookback**: 20 bars
- **Min observations**: 20
- **Normalization**: annualized
- **Timeframe**: any
- **Data required**: close
- **Missing behavior**: NaN → confidence downgrade

## Feature: atr_normalized
- **Formula**: ATR(14) / close
- **Lookback**: 14 bars
- **Min observations**: 15
- **Normalization**: ratio
- **Timeframe**: any
- **Data required**: high, low, close
- **Missing behavior**: NaN → confidence downgrade

## Feature: ma_spread
- **Formula**: (EMA20 - EMA60) / EMA60
- **Lookback**: 60 bars
- **Min observations**: 60
- **Normalization**: ratio
- **Timeframe**: any
- **Data required**: close
- **Missing behavior**: NaN → confidence downgrade

## Feature: range_efficiency
- **Formula**: (close - min(close, 20)) / (max(close, 20) - min(close, 20))
- **Lookback**: 20 bars
- **Min observations**: 20
- **Normalization**: [0,1]
- **Timeframe**: any
- **Data required**: close
- **Missing behavior**: 0.5 (neutral) → confidence downgrade

## Feature: shock (supplementary)
- **Formula**: |returns|
- **Lookback**: 1 bar
- **Min observations**: 2
- **Normalization**: raw
- **Timeframe**: any
- **Data required**: close
- **Missing behavior**: NaN → stress confidence downgrade

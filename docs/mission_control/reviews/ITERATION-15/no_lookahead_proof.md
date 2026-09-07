# No-Lookahead Proof — Iteration 15

## Method

Prefix invariance test: classification at bar T is identical whether computed
using bars 0..T or bars 0..T+N for any N > 0.

## Tests

### T2: Prefix invariance
- `test_prefix_invariance`: Bar 70 classification identical with 80 vs 100 bars
- `test_prefix_invariance_multiple_timestamps`: Bars 65, 70, 75, 80 all stable
- `test_prefix_invariance_appending_bars`: Appending 20 extra bars doesn't change T=70

### F5: Future leakage
- `test_no_future_leakage`: Full dataset result at T=70 matches prefix-only result
- `test_compute_features_no_lookahead`: Rolling features have NaN for initial bars

## Implementation Guarantee

All features use trailing-only computation:
- `pd.rolling(window=N)` — only looks back N bars
- `pd.ewm(span=N)` — exponentially weighted, past-only
- `pd.shift(1)` — lagged, no future data
- `np.log(close / close.shift(1))` — return from previous bar only

## Result

**PASS**: No future leakage detected. Classification at any timestamp T uses
only data available at or before T.

# Experiment Summary — Iteration 23C
**Date:** 2026-08-30

## Total Campaign Experiments

| Metric | Count |
|--------|-------|
| Planned | 0 |
| Executed | 0 |
| Skipped | 0 |
| Eligible | 0 |
| Rejected | 0 |
| Errors | 0 |

## Prior Experiment Memory (pre-23C)

| Metric | Count |
|--------|-------|
| Total families | 340 |
| Total instances | 460 |
| Tested (valid) | 0 |
| Error | 300 |
| Tested (with data) | 160 |

## Error Analysis

All 300 error instances failed with:
```
TypeError: run_backtest() missing 1 required positional argument: 'params'
```

This is a **backtest engine bug** — the `run_backtest()` function signature changed
but the experiment runner was not updated to match.

## By Family (GAZP/SBER scope)

| Family | GAZP Experiments | SBER Experiments | Valid Results |
|--------|-----------------|-----------------|---------------|
| sma_cross | 2 (error) | 0 | 0 |
| bollinger_reversion | 2 (error) | 0 | 0 |
| rsi_reversal | 2 (error) | 0 | 0 |
| atr_breakout | 2 (error) | 0 | 0 |
| macd_trend | 2 (error) | 0 | 0 |
| **Total GAZP** | **10** | **0** | **0** |

**SBER has zero experiment instances in memory.**

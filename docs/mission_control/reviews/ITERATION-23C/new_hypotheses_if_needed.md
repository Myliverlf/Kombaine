# New Hypotheses (If Needed) — Iteration 23C
**Date:** 2026-08-30

## Status

**NOT_GENERATED**

Per directive section 22, new hypotheses are only produced if all existing families are
exhausted with no candidate. While no candidate exists, the existing families have
NOT been exhausted — they have not been tested at all due to the engine bug.

## Exhaustion Assessment

| Family | Tested? | Exhausted? |
|--------|---------|------------|
| sma_cross | No (error) | No |
| bollinger_reversion | No (error) | No |
| rsi_reversal | No (error) | No |
| atr_breakout | No (error) | No |
| macd_trend | No (error) | No |
| ... (35 more) | No | No |

**All 40 families are untested, not exhausted.**

## Recommendation

Fix the backtest engine first. Then exhaust existing families before proposing
new hypotheses.

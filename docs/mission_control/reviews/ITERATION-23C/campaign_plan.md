# Campaign Plan — Iteration 23C
**Date:** 2026-08-30

## Budget Allocation

| Category | Planned | Executed | Skipped | Reason |
|----------|---------|----------|---------|--------|
| A. Revalidation of strongest evidence | 500 | 0 | 500 | Engine bug — no valid evidence exists |
| B. Promising-neighborhood exploration | 600 | 0 | 600 | Engine bug — cannot run backtests |
| C. Novel configs | 500 | 0 | 500 | Engine bug — cannot run backtests |
| D. Material rule variants | 400 | 0 | 400 | Engine bug — cannot run backtests |
| **Total** | **2,000** | **0** | **2,000** | |

## Execution Status

**RESEARCH_INFRASTRUCTURE_BLOCKED**

The backtest engine (`run_backtest()`) has a fatal TypeError that prevents any experiment
from completing successfully. All 300 existing experiments in experiment_memory.db ended
in error status.

## Family Distribution Plan (unexecuted)

| Family | Instrument | Timeframe | Planned | Executed |
|--------|-----------|-----------|---------|----------|
| sma_cross | GAZP | 15m | 50 | 0 |
| sma_cross | GAZP | 1h | 50 | 0 |
| sma_cross | SBER | 15m | 50 | 0 |
| sma_cross | SBER | 1h | 50 | 0 |
| bollinger_reversion | GAZP | 15m | 50 | 0 |
| bollinger_reversion | SBER | 15m | 50 | 0 |
| rsi_reversal | GAZP | 15m | 50 | 0 |
| rsi_reversal | SBER | 15m | 50 | 0 |
| ... (all 40 families) | ... | ... | ... | 0 |

## Deterministic Budget Rules

- Target: 2,000 distinct executable experiments
- Hard maximum: 5,000
- Actual safe budget: 0 (infrastructure blocked)
- STOP_EARLY_EXHAUSTED: N/A (never started)

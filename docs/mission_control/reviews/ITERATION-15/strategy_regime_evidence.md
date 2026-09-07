# Strategy Regime Evidence — Iteration 15

## Evidence Model

Strategy outcomes are mapped to the regime in which they occurred:

1. For each trade, look up regime at entry timestamp
2. Look up regime at exit timestamp
3. If exact timestamp match fails, find nearest within 48h
4. Record entry/exit regime dimensions + evidence class

## Evidence Classes (Distinct)

| Class | Meaning |
|-------|---------|
| BACKTEST | Historical backtest trade |
| WALK_FORWARD | Walk-forward validation trade |
| PAPER | Paper trading outcome |
| BROKER_REAL | Real broker execution |

## Regime-Specific Aggregates

Per regime bucket (trend + vol):
- trade_count
- win_rate
- gross_pnl, net_pnl
- profit_factor
- avg_trade, median_trade
- max_win, max_loss
- evidence_maturity (INSUFFICIENT / EARLY / USABLE / MATURE)

## Maturity Thresholds

| Trades | Maturity |
|--------|----------|
| < 5 | INSUFFICIENT |
| 5-19 | EARLY |
| 20-49 | USABLE |
| ≥ 50 | MATURE |

## Specialization Labels (Observational)

- PERFORMS_BETTER_IN
- PERFORMS_WORSE_IN
- NO_CLEAR_DIFFERENCE
- INSUFFICIENT_EVIDENCE

These are evidence summaries, NOT trading instructions.
No ENABLE/DISABLE strategy actions.

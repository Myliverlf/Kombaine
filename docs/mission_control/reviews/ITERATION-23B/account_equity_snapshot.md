# Account Equity Snapshot — Iteration 23B
**Date:** 2026-08-30T13:30:05.020241+00:00
**Account:** 2042640199
**Source:** READ_ONLY broker API (get_accounts, get_portfolio, get_orders)

## Cash Positions
| Currency | Quantity | Value (approx RUB) |
|----------|----------|---------------------|
| RUB | 20,191.20 | 20,191.20 |
| USD | 0.80 | ~68.48 (at 85.6 RUB/USD) |
| EUR | 0.55 | ~54.83 (at 99.68 RUB/EUR) |

## Equity Securities
| FIGI | Instrument | Shares | Avg Cost | Current | PnL |
|------|-----------|--------|----------|---------|-----|
| BBG004S681W1 | LKOH | 4 | 235.80 | 181.50 | -218.80 |

## Total Account Equity (Estimated)
```
Cash RUB:        20,191.20
Cash USD (est):     68.48
Cash EUR (est):     54.83
Equity LKOH:      726.00 (4 × 181.50)
─────────────────────────
Total:          21,040.51 RUB (approx)
```

## Available Cash
- RUB: 20,191.20 (unblocked)
- USD/EUR: Not applicable to MOEX equity trading
- Blocked: 0 (no open orders)

## Capital Base for LIVE_RISK_V1
```
capital_base = ~21,040 RUB (full account equity)
```

## Risk Amounts (LIVE_RISK_V1)
```
max_gross_exposure (10%):      ~2,104 RUB
max_risk_per_trade (0.25%):       ~53 RUB
max_strategy_risk (0.50%):       ~105 RUB
max_total_risk (0.50%):          ~105 RUB
daily_loss_halt (1.00%):         ~210 RUB
weekly_loss_halt (2.00%):        ~421 RUB
drawdown_halt (5.00%):         ~1,052 RUB
```

## Currency
Primary: RUB. USD/EUR holdings are not material for MOEX equity trading scope.

## Broker Truth Verified: YES
Account equity derived from verified READ_ONLY broker snapshot.

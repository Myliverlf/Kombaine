# Broker Read-Only Runtime — Iteration 23B
**Date:** 2026-08-30T13:30:05.020241+00:00

## Connection Attempt
- SDK: tinkoff-invest (imported successfully)
- Account: 2042640199 (Брокерский счёт)
- Account type: BROKER (type=1)
- Account status: ACTIVE (status=2)
- Opened: 2020-09-15

## Methods Called (ALL READ_ONLY)
| Method | Status | Result |
|--------|--------|--------|
| get_accounts | OK | 1 account found, identity verified |
| get_portfolio | OK | 4 positions returned |
| get_orders | OK | 0 open orders |
| get_positions | ERROR | API attribute mismatch (non-critical) |

## Mutating Calls Count: **0**

## No mutating broker request types were invoked:
- PostOrderRequest: NOT CALLED
- CancelOrderRequest: NOT CALLED
- ReplaceOrderRequest: NOT CALLED
- PostStopOrderRequest: NOT CALLED

## Portfolio Snapshot (READ_ONLY)
| FIGI | Type | Qty | Avg Price (RUB) | Current Price (RUB) | Expected Yield |
|------|------|-----|-----------------|---------------------|----------------|
| RUB000UTSTOM | currency | 20191.20 | 1.0 | 1.0 | 0 |
| USD800UTSTOM | currency | 0.8 | 78.3 | 85.6 | +6.06 |
| BBG0013HJJ31 | currency | 0.55 | 89.93 | 99.68 | +5.36 |
| BBG004S681W1 (LKOH) | share | 4 | 235.8 | 181.5 | -218.8 |

## Open Orders: 0

## Broker Truth Status: **PROVEN**
Account identity verified. Portfolio read. Orders read. Zero mutations.

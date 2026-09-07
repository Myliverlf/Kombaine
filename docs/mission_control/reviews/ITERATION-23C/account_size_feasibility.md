# Account Size Feasibility — Iteration 23C
**Date:** 2026-08-30

## Account Equity (READ_ONLY from broker)

| Component | Value (RUB) |
|-----------|-------------|
| Cash RUB | 20,191.20 |
| Cash USD (est) | 68.48 |
| Cash EUR (est) | 54.83 |
| LKOH 4 shares | 726.00 |
| **Total equity** | **~21,040** |

## Feasibility Analysis

| Constraint | Limit | Value | Status |
|-----------|-------|-------|--------|
| Max 10% gross exposure | 2,104 RUB | N/A | Not reached |
| Max 0.25% risk/trade | 52.60 RUB | N/A | Not reached |
| One concurrent position | 1 | 0 | OK |
| No leverage | 0x | N/A | OK |
| Valid lot size | Per instrument | N/A | Not reached |

## Lot Size Analysis

| Instrument | Lot Size | Min Entry (10% equity) | Min Entry (0.25% risk) |
|-----------|----------|----------------------|----------------------|
| GAZP | 10 shares | ~1,800 RUB | ~45 RUB (stop-dependent) |
| SBER | 10 shares | ~2,500 RUB | ~62.50 RUB (stop-dependent) |

## Verdict

**NOT_LIVE_FEASIBLE_ACCOUNT_SIZE** — No candidate exists to size.
Account equity ~21,040 RUB is very small; lot sizes may constrain feasibility.

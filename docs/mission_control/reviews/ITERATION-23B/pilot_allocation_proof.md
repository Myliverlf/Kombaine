# Pilot Allocation Proof — Iteration 23B
**Date:** 2026-08-30
**Status:** DO NOT TRADE — allocation calculation only

## Capital Base
```
account_equity ≈ 21,040 RUB (from verified broker snapshot)
```

## Allocation Formula (directive §16)
```
position_notional_cap = min(
    10% account equity,
    size allowed by 0.25% planned-loss risk,
    strategy-specific max size,
    liquidity limit,
    Risk Gate output
)
Round down to valid lot size.
If result < 1 lot: NO_TRADE
Never increase risk just to reach one lot.
```

## Calculation (for reference only)
```
10% of equity:     2,104 RUB
0.25% risk cap:       53 RUB (max planned loss, not notional)
Max strategy risk:   105 RUB (0.50%)
```

For LKOH at ~181.50 RUB/share:
```
10% notional = 2,104 / 181.50 = 11.6 shares → 11 shares (round down)
But 0.25% risk = 53 RUB planned loss
  → If 2x ATR stop = ~40 RUB/share loss → 53/40 = 1.3 lots max
  → Effective cap: 1 lot
```

## Result
```
NO_TRADE
Reason: NO_LIVE_STRATEGY_ELIGIBLE
```

Even if a strategy were eligible:
- One-position pilot: max 1 position
- Existing LKOH position on broker conflicts with pilot constraint
- Allocation would need to account for existing position

## DO NOT TRADE
This is a mathematical proof, not a trading instruction.
No order is placed or intended.

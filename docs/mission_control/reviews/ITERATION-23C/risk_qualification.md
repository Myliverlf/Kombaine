# Risk Qualification — Iteration 23C
**Date:** 2026-08-30

## Risk Qualification Status

**NOT_PERFORMED**

No strategy reached risk qualification because no valid backtest exists.

## LIVE_RISK_V1 (authoritative)

| Parameter | Value | Status |
|-----------|-------|--------|
| Policy ID | LIVE_RISK_V1 | Active |
| capital_base_method | FULL_ACCOUNT_EQUITY | ✅ |
| max_gross_exposure | 10% of equity | ✅ |
| max_single_position | 10% of equity | ✅ |
| max_risk_per_trade | 0.25% of equity | ✅ |
| max_strategy_risk | 0.50% of equity | ✅ |
| max_total_risk | 0.50% of equity | ✅ |
| daily_loss_halt | 1.00% of SOD equity | ✅ |
| weekly_loss_halt | 2.00% of SOW equity | ✅ |
| drawdown_halt | 5.00% from HWM | ✅ |
| max_concurrent_positions | 1 | ✅ |
| leverage | NO | ✅ |
| averaging_down | NO | ✅ |
| pyramiding | NO | ✅ |
| shorting | NO | ✅ |
| immutable | true | ✅ |

## Risk Boundary Requirements (unmet)

For any candidate:
- [ ] Entry method defined
- [ ] Exit method defined
- [ ] Stop/loss boundary deterministic
- [ ] Sizing method documented
- [ ] Overnight/gap behavior specified
- [ ] Invalid-market conditions defined
- [ ] Maximum holding period set
- [ ] Planned loss ≤ 0.25% account equity

## Verdict

**NOT_LIVE_ELIGIBLE_RISK_UNDEFINED** — No candidate has a defined risk boundary.

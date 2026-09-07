# Live Risk Policy Status — Iteration 23

**Date:** 2026-08-30

## Status: REQUIRED_HUMAN_POLICY

### Current Risk Configuration (config.json)
```json
{
  "risk_per_trade_pct": 2.7,
  "go_budget_pct": 50,
  "max_slots": 2,
  "portfolio_stop_drawdown_pct": 25,
  "slot_eject_pf": 0.9,
  "slot_eject_window_trades": 20,
  "slot_eject_streak_stops": 3,
  "slot_eject_slot_drawdown_pct": 15,
  "slot_eject_silent_days": 5,
  "promotion_margin_pct": 10,
  "max_contracts_per_entry": 1
}
```

### What Exists
- Risk parameters in config.json
- Risk scorecard weights defined
- Portfolio stop drawdown: 25%
- Per-trade risk: 2.7% of deposit
- Max contracts per entry: 1

### What Does NOT Exist (Required for Live)
- **No explicit live risk cap** (absolute ₽ amount or % of capital for live trading)
- **No max loss per day/week/month** for live
- **No position size limits** beyond max_contracts_per_entry
- **No drawdown circuit breaker** for live (only paper stop)
- **No max concurrent losses** policy
- **No escalation thresholds** for live losses

### Decision
**REQUIRED_HUMAN_POLICY**

The human owner MUST define explicit live risk limits before any live authorization:
1. Max ₽ at risk per trade (absolute amount)
2. Max ₽ total exposure (absolute amount)
3. Max daily loss (absolute amount)
4. Max drawdown before halt (absolute amount or %)
5. Escalation thresholds (when to notify human)
6. Halt conditions (when to stop all trading)

### Warning
DO NOT invent risk amounts. The config.json risk parameters are for paper/dryrun mode. Live risk policy must be explicitly defined by human owner.

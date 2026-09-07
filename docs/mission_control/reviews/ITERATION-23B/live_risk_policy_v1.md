# LIVE_RISK_V1 — Canonical Live Risk Policy
**Policy ID:** LIVE_RISK_V1
**Version:** 1.0.0
**Effective:** 2026-08-30
**Owner Approval:** HERMES_MISSION_CONTROL_23B directive §2
**Scope:** First controlled-live pilot
**Storage:** state/live_risk/LIVE_RISK_V1.json (authoritative, immutable)

## Risk Limits

| Parameter | Value | Basis |
|-----------|-------|-------|
| capital_base_method | FULL_ACCOUNT_EQUITY | Verified broker account equity |
| max_gross_exposure | 10% of account equity | PILOT cap, not permanent |
| max_single_position_exposure | 10% of account equity | One-position pilot |
| max_risk_per_trade | 0.25% of account equity | Planned loss entry→exit |
| max_open_strategy_risk | 0.50% of account equity | Stricter per-trade dominates in pilot |
| max_total_open_risk | 0.50% of account equity | Portfolio aggregate |
| daily_loss_halt | 1.00% of start-of-day equity | NO_NEW_ORDERS + ESCALATE_HUMAN |
| weekly_loss_halt | 2.00% of start-of-week equity | BLOCK + HUMAN_REVIEW |
| drawdown_halt | 5.00% from HWM | NO_NEW_ENTRIES + INCIDENT |
| max_concurrent_positions | 1 | Pilot constraint |

## Absolute Prohibitions

| Rule | Value |
|------|-------|
| leverage_allowed | NO |
| borrowed_cash | NO |
| margin_usage | NO |
| shorting | NO |
| averaging_down | NO |
| pyramiding | NO |

## Risk Hierarchy

```
GLOBAL SAFETY HALT
→ BROKER TRUTH / RECONCILIATION
→ ACCOUNT / PORTFOLIO RISK LIMITS
→ STRATEGY-SPECIFIC RISK RULES
→ ALLOCATION POLICY
→ SIGNAL
→ EXECUTION
```

Strategy may be stricter, never looser than LIVE_RISK_V1.
Risk Gate remains final authority.

## Kill/Halt Semantics

- Daily breach: NO NEW ORDERS + ESCALATE HUMAN
- Weekly breach: BLOCK NEW ENTRIES + REQUIRE HUMAN REVIEW
- Drawdown breach: NO NEW LIVE ENTRIES + HIGH-PRIORITY INCIDENT + REQUIRE HUMAN REVIEW
- Auto-liquidation: NEVER (unless separately governed position-risk rule requires exit)
- Risk Gate override: ALLOCATION may reduce size or block, never override a VETO

## Data & Broker Requirements

- Data quality: CERTIFIED_FOR_LIVE_SCOPE for pilot instrument
- Broker truth: READ_ONLY_PROVEN, account equity verified
- Liquidity: FAIL_CLOSED — UNKNOWN → BLOCK_LIVE
- Position state: MUST be read from broker, never assumed FLAT

## Authorization

- LIVE_RISK_V1 is policy configuration, NOT execution permission
- Human-only authorization contract preserved from Iteration 23
- No authorization issued in 23B
- Agent/system/MC/Telegram cannot authorize live

# Owner Capital Policy — Iteration 23B
**Date:** 2026-08-30
**Source:** Owner-approved business intent (Iteration 23B directive §2)

## Decision
```
capital_base_policy = FULL_ACCOUNT_EQUITY
```

## Meaning
- The full cash/equity available on the brokerage account may be considered
  the CAPITAL BASE available to the trading system.
- This does NOT mean the system must keep 100% of the account continuously invested.
- 100% ACCOUNT CAPITAL AVAILABLE ≠ 100% SIMULTANEOUS MARKET EXPOSURE.
- Cash is a valid state whenever no strategy passes evidence/risk gates.

## Encoded Rules
```text
capital_base = current verified brokerage account net liquidation/equity
capital_base != target_exposure
capital_base != mandatory_exposure
capital_base != permission_to_use_leverage
```

## Risk at Multiple Levels
- Portfolio/account level: global exposure caps, drawdown halts, daily/weekly loss limits.
- Individual strategy/position level: per-trade risk cap, per-strategy open risk cap.

## Conservative Optimization Mandate
- The system must optimize allocation conservatively.
- The system must NEVER weaken the Risk Gate merely to deploy more capital.
- Never increase risk just to reach one lot.

## Capital Pool Interpretation
```
THE SYSTEM MAY USE THE FULL ACCOUNT AS THE CAPITAL POOL OVER TIME.
```
Must NOT encode as:
```
ALWAYS BE 100% INVESTED
OPEN WITH 100% NOTIONAL
IGNORE CASH BUFFER
IGNORE RISK
```

Long-term utilization may rise only after real execution/reconciliation/attribution
evidence and a new policy version.

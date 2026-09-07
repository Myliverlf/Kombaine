# Abort Conditions — Iteration 23B
**Date:** 2026-08-30

## Future No-New-Order Conditions
Any of these blocks ALL new live orders:

1. **broker truth UNKNOWN** — no verified broker snapshot
2. **reconciliation CONFLICTED** — broker vs local state mismatch
3. **data stale/bad** — pilot instrument data not certified fresh
4. **unexpected position** — position exists that system didn't open
5. **Risk Gate unhealthy** — risk engine not operational
6. **daily loss >= 1%** — daily loss halt triggered
7. **weekly loss >= 2%** — weekly loss halt triggered
8. **drawdown >= 5%** — portfolio drawdown halt triggered
9. **duplicate intent suspicion** — potential duplicate order
10. **UNKNOWN execution beyond policy** — unexplained broker activity
11. **authorization stale** — human authorization expired
12. **strategy evidence stale** — strategy evidence no longer fresh
13. **critical Mission Control incident** — system health critical
14. **source-of-truth ambiguity** — unclear what is the ground truth

## Behavior on Breach
- **NO new orders**
- **NO new strategy activation**
- **ESCALATE HUMAN** (Telegram/owner notification)
- **PRESERVE TRUTH** (do not modify broker state)

## Auto-Liquidation Rule
```
NEVER auto-liquidate unless a separately governed position-risk rule requires exit.
```

## Current Status
At Iteration 23B completion:
- broker truth: PROVEN (but reconciliation DEGRADED)
- daily/weekly/drawdown: N/A (no live trades yet)
- authorization: NOT ISSUED
- strategy eligibility: NO_LIVE_STRATEGY_ELIGIBLE

**All abort conditions are in default (safe) state.**

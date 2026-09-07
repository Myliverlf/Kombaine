# ADR-2026-08-30: Owner Live Risk Policy & Broker Truth Closure

**Date:** 2026-08-30
**Status:** ACCEPTED
**Deciders:** Owner (capital decision) + Hermes Mission Control (execution)
**Iteration:** 23B

## Context

Iteration 23 ended NOT_READY_FOR_LIVE with 5 hard blockers:
1. No broker connection
2. 0 eligible strategies
3. No explicit live risk policy
4. LKOH 1095d data duplicate
5. Position state UNKNOWN

The owner has now supplied capital intent and approved the full account equity as the capital base. This ADR codifies the owner's capital decision, establishes the versioned live risk policy, achieves broker truth closure, and recertifies readiness.

## Decision

### Owner Capital Decision
```
capital_base_policy = FULL_ACCOUNT_EQUITY
```
- Full cash/equity available as denominator for allocation/risk
- Does NOT mandate 100% simultaneous market exposure
- Risk at portfolio AND position levels
- Conservative optimization; never weaken Risk Gate

### LIVE_RISK_V1 Policy
Canonical versioned risk policy created and stored at:
- `state/live_risk/LIVE_RISK_V1.json` (authoritative, immutable)
- `docs/mission_control/reviews/ITERATION-23B/live_risk_policy_v1.md` (readable)

Key limits: 10% gross exposure, 0.25% per-trade risk, 0.50% portfolio risk,
1% daily halt, 2% weekly halt, 5% drawdown halt, max 1 position.
No leverage, no averaging down, no pyramiding.

### Broker Truth Closure
- READ_ONLY broker connection achieved via tinkoff-invest SDK
- Account 2042640199 verified (Брокерский счёт, opened 2020-09-15)
- Portfolio: 4 positions (RUB cash + USD + EUR + LKOH 4 shares)
- Open orders: 0
- Mutating calls: 0
- Position state: NON_FLAT (LKOH pre-existing)

### Tradable Universe
- TIER_1: GAZP, LKOH, SBER
- RESTRICTED: BR, Si (no auto-expansion)

### Strategy Selection
NO_LIVE_STRATEGY_ELIGIBLE — no strategy has sufficient walk-forward evidence
+ risk contract for live. Thresholds NOT weakened.

## Consequences

### Resolved
- Owner capital decision codified ✅
- LIVE_RISK_V1 versioned and stored ✅
- Broker truth proven (READ_ONLY) ✅
- Account equity verified (~21,040 RUB) ✅
- Reconciliation: DEGRADED (expected — external position) ✅
- Position state: NON_FLAT (known, documented) ✅
- Data certification: GAZP/SBER certified, LKOH conditional ✅
- 58 new tests all passing ✅

### Remaining
- No eligible strategy for pilot (G4 FAIL)
- LKOH 1095d data duplicate unresolved
- Human authorization not issued (by design)
- Live execution blocked (by design)

### Final Readiness
```
NOT_READY
```

## Next Steps
1. Strategy factory must produce evidence-certified candidates with risk contracts
2. Resolve LKOH 1095d data duplicate
3. Iteration 24: market-open preflight + fresh broker snapshot + human authorization

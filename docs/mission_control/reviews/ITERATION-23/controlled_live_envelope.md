# Controlled-Live Envelope — Iteration 23

**Date:** 2026-08-30

## STATUS: PROPOSED — NOT ACTIVATED

### Smallest Evidence-Supported Envelope

| Parameter | Value | Justification |
|-----------|-------|---------------|
| Broker accounts | 1 | 2042640199 (Tinkoff) |
| Instruments | 1 | TBD (from GAZP/SBER certified scope) |
| Strategies | 1 | TBD (highest-ranked eligible — currently NONE) |
| Max position count | 1 | Conservative single-position pilot |
| Concurrent replacement | NONE | No position rotation during pilot |
| Portfolio rotation | NONE | Fixed portfolio during pilot |
| Human authorization | REQUIRED | Manual, explicit, per-trade or per-session |
| Operator present | YES | Human must be monitoring |
| Session/window | BOUNDED | Must have start/end time |
| Risk Gate | MANDATORY | Must pass before every order |
| Post-trade reconciliation | MANDATORY | After every fill |
| Halt on invariant failure | YES | Immediate stop on any invariant breach |

### Exclusions
- BR/Si: Excluded (1095d data missing)
- LKOH 1095d: Degraded (duplicate of 365d)
- Auto-liquidation: NEVER under any circumstances
- Mode change: Remains paper until explicit human authorization

### Prerequisites Before Activation
1. ✅ Data certified for exact scope (GAZP/SBER)
2. ⬜ Broker credentials configured and live connection proven
3. ⬜ Broker reconciliation completed
4. ⬜ Broker position state known (FLAT or NON_FLAT)
5. ⬜ Human-defined live risk policy
6. ⬜ Telegram configured and tested
7. ⬜ Strategy with sufficient evidence exists
8. ⬜ Human authorization issued
9. ⬜ Order deduplication implemented
10. ⬜ Restart recovery implemented

### Current Blockers
1. **NO_STRATEGY_ELIGIBLE** — Zero strategies pass eligibility thresholds
2. **REQUIRED_HUMAN_POLICY** — No explicit live risk cap defined
3. **BROKER_TRUTH_UNKNOWN** — No live broker connection
4. **RECONCILIATION_INCOMPLETE** — Cannot reconcile without broker data
5. **ORDER_DEDUP_UNPROVEN** — Idempotency not guaranteed
6. **NO_HUMAN_AUTHORIZATION** — Authorization contract defined, not issued

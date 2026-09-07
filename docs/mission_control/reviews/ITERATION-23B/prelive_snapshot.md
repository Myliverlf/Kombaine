# Pre-Live Snapshot — Iteration 23B
**Snapshot ID:** snap_23B_20260830T133202
**Created:** 2026-08-30T13:32:00.958244+00:00
**Hash:** adf68840638efe18
**Iteration:** 23B

## Mode
- mode: paper
- paper_first: true
- LIVE_EXECUTE: DENIED
- LIVE_CANCEL: DENIED

## Broker Truth
- Status: PROVEN
- Account: 2042640199 (Брокерский счёт)
- Methods: get_accounts, get_portfolio, get_orders (ALL READ_ONLY)
- Mutating calls: 0
- Position state: NON_FLAT (LKOH 4 shares, pre-existing)

## Account Equity
- Total: ~21,040 RUB
- Cash RUB: 20,191.20
- LKOH: 4 shares at ~181.50

## Reconciliation
- Status: DEGRADED
- Reason: External LKOH position not in local state

## Data Quality
- GAZP: CERTIFIED
- SBER: CERTIFIED
- LKOH: CONDITIONAL (1095d duplicate)
- BR: BLOCKED
- Si: BLOCKED

## Live Risk Policy
- LIVE_RISK_V1 active (state/live_risk/LIVE_RISK_V1.json)
- Version: 1.0.0

## Pilot Strategy
- NO_LIVE_STRATEGY_ELIGIBLE

## Readiness Gates
| Gate | Status |
|------|--------|
| G1 Test Suite | PASS |
| G2 Config Safety | PASS |
| G3 Data Coverage | PASS |
| G4 Strategy Evidence | FAIL |
| G5 Portfolio State | PASS |
| G6 Broker Truth | PASS |
| G7 Risk | PASS |
| G8 Transition | PASS |
| G9 Recovery | PASS |
| G10 Observability | PASS |
| G11 Scheduling | PASS |
| G12 Test Full | PASS |

## Overall: NOT_READY

# Changed Files — Iteration 23B
**Date:** 2026-08-30

## New Files
| File | Description |
|------|-------------|
| `state/live_risk/LIVE_RISK_V1.json` | Canonical live risk policy (immutable) |
| `state/broker_truth_snapshot_23b.json` | READ_ONLY broker snapshot |
| `tests/test_live_preconditions_23b.py` | 58 T1-T30 tests |
| `docs/mission_control/reviews/ITERATION-23B/owner_capital_policy.md` | Owner capital decision |
| `docs/mission_control/reviews/ITERATION-23B/live_risk_policy_v1.md` | Human-readable risk policy |
| `docs/mission_control/reviews/ITERATION-23B/strategy_risk_contract.md` | Strategy risk contract |
| `docs/mission_control/reviews/ITERATION-23B/tradable_universe_policy.md` | Universe policy |
| `docs/mission_control/reviews/ITERATION-23B/liquidity_policy.md` | Liquidity policy |
| `docs/mission_control/reviews/ITERATION-23B/broker_readonly_runtime.md` | Broker truth evidence |
| `docs/mission_control/reviews/ITERATION-23B/account_equity_snapshot.md` | Account equity |
| `docs/mission_control/reviews/ITERATION-23B/reconciliation.md` | Reconciliation |
| `docs/mission_control/reviews/ITERATION-23B/broker_position_state.md` | Position state |
| `docs/mission_control/reviews/ITERATION-23B/data_scope_certification.md` | Data certification |
| `docs/mission_control/reviews/ITERATION-23B/pilot_strategy_selection.md` | Strategy selection |
| `docs/mission_control/reviews/ITERATION-23B/pilot_allocation_proof.md` | Allocation proof |
| `docs/mission_control/reviews/ITERATION-23B/abort_conditions.md` | Abort conditions |
| `docs/mission_control/reviews/ITERATION-23B/prelive_snapshot.md` | Pre-live snapshot |
| `docs/mission_control/reviews/ITERATION-23B/blockers.md` | Blockers |
| `docs/mission_control/reviews/ITERATION-23B/readiness_scorecard.md` | G1-G12 |
| `docs/mission_control/reviews/ITERATION-23B/tests.md` | Test summary |
| `docs/mission_control/reviews/ITERATION-23B/changed_files.md` | This file |
| `docs/mission_control/reviews/ITERATION-23B/final_report.md` | Final report |
| `docs/mission_control/decisions/ADR-2026-08-30-owner-live-risk-policy-and-broker-truth-closure.md` | ADR |

## Modified Files
| File | Change |
|------|--------|
| `state/prelive_snapshot.json` | Updated with 23B snapshot data |
| `tests/test_live_preconditions.py` | Fixed T8BrokerSnapshot to accept nested format |

## Unmodified Files
All production code/ modules: unchanged.
config.json: unchanged (mode=paper, paper_first=True preserved).

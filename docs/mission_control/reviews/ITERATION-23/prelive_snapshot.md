# Pre-Live Snapshot — Iteration 23

**Date:** 2026-08-30

## Snapshot ID
`snap_i23_final_live_preconditions`

## Contents

| Field | Value | Source |
|-------|-------|--------|
| mode | paper | config.json |
| paper_first | true | config.json |
| git_revision | e1acfafac73a | git log |
| config_hash | a0ee9273c9331c6b | SHA256 of config.json |
| registry_hash | c8b55702e5e82503 | SHA256 of strategy_registry.json |
| active_strategies | 0 | registry query |
| open_positions | 1 (LKOH_volatility_squeeze_15m) | portfolio.json |
| broker_truth_status | UNKNOWN | no live connection |
| data_quality_gazp | CERTIFIED_FOR_SCOPE | data audit |
| data_quality_lkoh | CONDITIONAL (1095d degraded) | data audit |
| data_quality_sber | CERTIFIED_FOR_SCOPE | data audit |
| data_quality_br | BLOCKED (excluded) | data audit |
| data_quality_si | BLOCKED (excluded) | data audit |
| fx_scope | NOT_MATERIAL_TO_CERTIFIED_SCOPE | RUB-only |
| strategy_evidence | NO_LIVE_STRATEGY_ELIGIBLE | 0 eligible |
| risk_policy | REQUIRED_HUMAN_POLICY | no live cap |
| allocation_policy | config.json:risk:go_budget_pct | existing |
| execution_policy | config.json:engine | existing |
| broker_account_id | 2042640199 | config.json |
| broker_token_exists | true | ~/.hermes/tinkoff.env |
| broker_live_connection | NOT_ESTABLISHED | no runtime test |
| broker_position_state | UNKNOWN | no live query |
| reconciliation | INCOMPLETE | no broker data |
| live_authorization_issued | false | contract defined only |
| human_actor_id | NOT_IDENTIFIED | no authorization |
| authorization_expiry | N/A | no authorization |
| telegram_status | NOT_CONFIGURED | no forwarding |
| test_status | 2029 collected | pytest --co |
| readiness_gates | G1-G12 versioned | see readiness_scorecard |
| abort_conditions | 11 defined | see abort_conditions.md |
| envelope_status | PROPOSED_NOT_ACTIVATED | see controlled_live_envelope |
| live_execute | DENIED | permission boundary |
| live_cancel | DENIED | permission boundary |
| iteration | 23 | this iteration |
| version | 1.0.0 | snapshot version |
| factory_version | 1.0.0 | strategy factory version |
| created_at | 2026-08-30TTHIS_ITERATION | ISO-8601 |

## Immutability
Any material change to mode, paper_first, git_revision, config_hash, registry_hash, data certification, broker truth, strategy evidence, risk policy, or authorization state invalidates this snapshot.

## Hash
To be computed after all fields finalized.

## File Location
`state/prelive_snapshot.json` (will be updated)

# 02_SOURCE_OF_TRUTH — strategy_combine

## Canonical Sources
1. **Strategy Registry**: `state/strategy_registry.json` — all strategy state (546 strategies)
2. **Portfolio**: `state/portfolio.json` — active slots and positions
3. **Transition Store**: `state/portfolio_transitions.db` — transition state machine
4. **Human Review**: `state/human_review.db` — governance decisions
5. **Lifecycle**: `state/strategy_lifecycle.db` — decay detection
6. **Production Truth**: `state/production_truth.db` — data integrity
7. **Certification**: `state/system_certification.db` — readiness evaluation state

## Transition Authority
`core/portfolio_transition.py` is the single authoritative transition path.
Legacy swap paths are routed through it or are non-authoritative.

## Certification Authority
`core/system_certification.py` is the single authoritative readiness evaluation path.
Certification results are persisted in `state/system_certification.db`.

## Last Updated
2026-08-30 — Iteration 21 End-to-End Certification

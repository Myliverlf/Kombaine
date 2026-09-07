# ADR-2026-08-30: Strategy Factory & Controlled-Live Preconditions

## Status
ACCEPTED

## Context
Iteration 21 certified the system as CONDITIONALLY_READY. Iteration 22 must:
1. Close factual prerequisites that kept certification conditional
2. Prove the canonical research factory actually produces, tests, remembers, revalidates and ranks new strategy/config candidates

## Decision
Implement Strategy Factory module with:
- StrategyFamily/StrategyHypothesis dataclasses
- ExperimentPlanner for deterministic daily plan generation
- WalkForwardProver for no-lookahead validation
- NewFamilyHypothesisInterface for safe proposal flow
- PreLiveSnapshot for immutable versioned state
- StopKillProcedure for operator control

## Consequences
- Strategy factory is operational and tested (56 new tests)
- Zero eligible candidates accepted as valid (no manufacturing)
- New family hypothesis cannot self-promote to production
- Walk-forward semantics proven (no lookahead)
- Pre-live snapshot created and immutable
- Stop/kill procedure documented (no auto-liquidation)
- System remains CONDITIONALLY_READY
- LIVE not activated
- Mode remains paper, paper_first remains true

## Evidence
- 56 tests pass (T1-T24)
- Pre-live snapshot: snap_a3d922548c3efb1a
- Eligibility hash: 1f0c6f6ed87b4285827787728c06fa3744900ae06a4c163b1e0aee5c0b94f054
- All safety invariants maintained

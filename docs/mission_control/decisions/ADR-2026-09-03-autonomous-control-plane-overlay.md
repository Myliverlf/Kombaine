# ADR-2026-09-03-autonomous-control-plane-overlay

**Status:** Proposed
**Date:** 2026-09-03
**Scope:** Hermes + strategy_combine autonomous improvement loop

---

## Context

The combine already has canonical run contracts, a stage machine, canonical registry/state, and mission-control docs. What it lacks is a single evidence-first control plane for autonomous improvement episodes.

The user wants Hermes to stop acting like a manual task router and instead become an autonomous engineering/research control plane that can:
- observe the current bottleneck;
- form hypotheses;
- plan bounded episodes;
- decompose only when needed;
- execute and test;
- critique with independent evidence;
- integrate or reject;
- measure progress;
- replan or stop.

---

## Decision

Introduce an **autonomous control-plane overlay** on top of the existing combine architecture.

### Key properties
- Hermes owns the canonical objective, state, evidence ledger and budgets.
- L1/L2/L3 are ephemeral roles, not permanent bureaucracy.
- Work is organized as bounded episodes.
- PASS requires evidence, not model confidence.
- Existing canonical systems remain authoritative: run contract, stage machine, strategy registry, portfolio transition, system certification.

---

## Consequences

### Positive
- Avoids adding a second orchestrator or memory system.
- Reduces intent loss compared to rigid hop-by-hop hierarchies.
- Keeps dynamic decomposition while preserving canonical truth.
- Supports meta-review, loop detection and resource governance.

### Negative
- Requires formal contracts for objective, task, result, evidence and state.
- Requires careful staleness handling and lock discipline.
- Needs a clear mapping from the overlay to the existing research pipeline.

---

## Implementation direction

Create the control-plane docs first, then map them onto existing canonical modules without changing trading logic.

Files added in this session:
- `docs/mission_control/architecture_reset/autonomous_control_plane.md`
- `docs/mission_control/architecture_reset/control_plane_contracts.md`
- `docs/mission_control/architecture_reset/autonomy_state_memory.md`

---

## Status

This ADR approves the architecture direction for implementation planning, not code execution.

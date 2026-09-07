# Autonomous Control Plane for Hermes + strategy_combine

**Status:** draft for implementation
**Scope:** architecture only; no trading logic changes, no broker mutations
**Goal:** turn Hermes into the single control plane that manages an evidence-first autonomous improvement loop for the trading combine.

---

## 1. Core decision

Do **not** implement a rigid permanent `Hermes → L1 → L2 → L3` bureaucracy.
Instead, make Hermes the canonical control plane and create **ephemeral roles** only when a bounded episode needs decomposition.

### Chosen architecture

```text
Evgeniy
  ↓ vision / constraints / priorities / stop conditions
Hermes Control Plane
  ↓ objective + budgets + state + evidence + policy
Adaptive Work Graph
  ├─ strategic roles (L1)
  ├─ decomposition workstreams (L2)
  └─ atomic executors (L3)
  ↓
Tools / code / tests / data / environment
```

Hermes owns:
- canonical objective;
- immutable evidence ledger;
- budgets;
- state transitions;
- escalation rules;
- stop conditions;
- learning history.

Agents own only short-lived work.

---

## 2. Why this design

### Why not a fixed 3-level hierarchy
- Every hop dilutes intent.
- Every hop can amplify hallucination.
- Every hop creates false PASS risk.
- Fixed hierarchies turn into bureaucracy.

### Why not a free dynamic graph
- Dynamic graphs without a control plane create stale state, races, duplicated work, and runaway fan-out.

### Why this hybrid works
- Hermes remains the only source of truth.
- Agents are spawned only when a task needs bounded decomposition.
- Evidence and state live outside the agents.
- PASS is granted only by Hermes after evidence checks.

---

## 3. Roles

### Hermes
Responsibilities:
- receive vision/constraints/quality rules;
- detect the current bottleneck;
- select the next hypothesis worth testing;
- allocate budgets;
- approve / reject / escalate;
- persist the canonical state;
- stop unproductive loops.

### L1 — strategic reasoning roles
Use only when the problem needs higher-level judgment.
Typical roles:
- Architect / Planner
- Governor / Budget Checker
- Integrator / Decision Maker

L1 does not own state.
It only transforms objective context into a bounded work plan.

### L2 — workstream roles
Use for a narrow program of work.
Typical roles:
- research
- implementation
- validation
- adversarial review
- performance analysis

L2 receives a scoped objective, not an open-ended chat.

### L3 — atomic executors
Use for single-purpose tasks:
- inspect file
- run test
- collect evidence
- compare outputs
- isolate bug
- generate artifact

L3 must return structured evidence, not “done”.

---

## 4. Control contracts

### Context contract
Every agent receives only:
- objective_id
- scope
- constraints
- current state snapshot
- relevant evidence
- budget
- success criteria
- failure criteria
- allowed tools

### Task contract
Every task must define:
- what to do
- why it matters
- what evidence counts
- what would falsify it
- how much budget it gets

### Result contract
Every result must include:
- task
- action
- evidence
- files changed
- tests run
- failures
- uncertainties
- recommendation
- confidence

### Evidence contract
Every material claim must include:
- claim
- evidence
- source
- reproducibility
- limitations
- confidence

No evidence, no PASS.

---

## 5. State architecture

### Canonical state
Hermes must keep a single canonical state with these buckets:
- core memory: unchanging intent, constraints, style, stop rules
- project memory: architecture, components, truths, boundaries
- decision memory: accepted decisions and why
- experiment memory: hypothesis, test, result
- failure memory: prior dead ends and failure modes
- working memory: current episode only

### Source of truth
The following must be authoritative:
- canonical registry / run manifest / evidence ledger / state machine / policy files

Derived views must remain derived views.
They may not silently override canonical state.

### Staleness handling
If a worker sees stale state, it must fail closed and request a fresh snapshot.

---

## 6. Autonomous loop

The loop is not “keep going forever”.
It is a bounded episode loop:

```text
OBSERVE
→ identify bottleneck
→ form hypothesis
→ score value / cost / risk
→ plan bounded experiment
→ decompose only if needed
→ execute
→ test
→ verify evidence independently
→ critique
→ integrate or reject
→ measure delta
→ learn
→ replan or stop
```

Stop when:
- evidence is sufficient;
- uncertainty is reduced enough;
- improvement is proven;
- hypothesis is falsified;
- budget is spent;
- the same failure repeats.

---

## 7. Loop detection and anti-bureaucracy rules

### Detect these patterns
- same hypothesis repeated
- same test repeated with no new evidence
- same failure class repeated
- same plan rewritten without execution
- more agents without more signal
- fake PASS / self-validation
- consensus replacing proof

### Required response
- increment no-progress counter
- switch to meta-review after N failures
- shrink scope or kill the episode
- escalate to Hermes with a discriminating test

### Anti-bureaucracy rule
Do not create a new role unless it pays for itself in evidence gain.
If the role is only “coordination”, Hermes should do it.

---

## 8. Review and critique

No agent may certify its own output.

Required independent checks:
- builder
- critic
- validator
- integrator

A result is accepted only when independent evidence passes the defined acceptance criteria.

---

## 9. Resource governor

Every episode must have explicit limits:
- max tokens
- max iterations
- max wall time
- max parallel agents
- max retries
- max failed attempts

The governor may increase budget only if the episode is still producing evidence.
Otherwise it shuts the episode down.

---

## 10. Failure and recovery

If a worker fails:
- preserve its state;
- record the failure class;
- do not lose evidence;
- do not auto-retry forever;
- route to a discriminating check or escalation.

If the whole loop stalls:
- freeze current episode;
- run a meta-review;
- either change approach or stop.

---

## 11. Model allocation

### Suggested mapping
- **GPT-5.4 mini**: L1 strategic reasoning, critique, synthesis, meta-review
- **Mu Spark 1.3**: L2 decomposition, research program planning, structured workstream management
- **Other available models**: L3 atomic execution, extraction, testing, logging, formatting

### Rule
Route by complexity, not by title.
Use expensive reasoning only where uncertainty is high.
Use cheaper models for narrow deterministic work.

---

## 12. Relationship to existing Hermes / combine

Reuse before creating new.
The architecture should extend what already exists:
- canonical run contract
- stage machine
- canonical registry
- candidate registry
- evidence contracts
- mission control docs
- portfolio transition machine
- system certification

Do **not** create a second orchestrator, second memory system, or second kanban.

---

## 13. Implementation boundary

This document is only the control-plane design.
Before coding, map the current system into these four primitives:
- state
- scheduler
- evidence
- resource governance

If any of these are missing, add them by extending current modules, not by duplicating the stack.

---

## 14. Implementation order

1. formalize canonical objective / episode contract
2. formalize evidence contract
3. formalize state snapshot + staleness rules
4. formalize budgets / governor
5. wire autonomous loop to existing research pipeline
6. add meta-review and stop rules
7. only then add new agent roles if needed

---

## 15. Open questions

- Which module should own the canonical episode state?
- Which current reports are canonical vs derived?
- Which decisions are allowed to auto-apply?
- Which evidence artifacts are sufficient for PASS?
- What exact budget thresholds should trigger stop vs escalation?

---

## 16. Verdict

This is the architecture to implement next:

- Hermes = control plane
- agents = ephemeral bounded workers
- evidence = first-class
- state = canonical and immutable where needed
- loop = bounded, not infinite

**This is ready for implementation design, not for direct coding yet.**

# Autonomous Hermes — State and Memory Design

**Purpose:** avoid context dilution and stale-state bugs by separating memory into layers.

---

## 1. Core memory

Stable facts that should not change often.
Examples:
- user preferences
- permanent safety rules
- strategic vision
- platform limits
- hard constraints

---

## 2. Project memory

Project-level facts about the combine.
Examples:
- architecture map
- canonical files
- component boundaries
- known invariants
- truth hierarchy

---

## 3. Decision memory

What was decided and why.
Examples:
- architecture decisions
- rejected alternatives
- governance rules
- model routing choices

Must include:
- date
- decision
- rationale
- alternatives rejected
- evidence used

---

## 4. Experiment memory

Hypotheses and their outcomes.
Examples:
- strategy experiments
- agent experiments
- routing experiments
- loop tests

Must include:
- hypothesis
- method
- evidence
- result
- interpretation
- next action

---

## 5. Failure memory

What already failed and why.
Examples:
- dead ends
- false PASS patterns
- stale state incidents
- duplicate work patterns
- loop failures

This layer is critical for anti-repeat and loop detection.

---

## 6. Working memory

Only the current episode.
Examples:
- active tasks
- current snapshot
- current budget
- current warnings
- current blockers

Working memory must expire at episode end.

---

## 7. Canonical state vs derived views

Hermes must always distinguish:
- canonical state
- derived summary
- cached view

Derived views can help; they can never silently become truth.

---

## 8. Staleness rules

A worker must reject stale context when:
- the underlying objective changed
- evidence changed
- state version changed
- a budget or stop condition changed
- a conflict was resolved elsewhere

Staleness must fail closed.

---

## 9. Memory update rules

Only write memory when something is stable and reusable.
Do not store:
- temporary progress
- one-off task chatter
- transient logs
- incomplete speculation

Store:
- stable decisions
- validated lessons
- persistent constraints
- recurring failure patterns

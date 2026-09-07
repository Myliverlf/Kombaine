# Control Plane Contracts for Autonomous Hermes

**Purpose:** define the minimal contracts needed for an evidence-first autonomous loop.

---

## 1. Objective contract

A run begins with a single canonical objective.

Fields:
- objective_id
- owner
- target_system
- scope
- out_of_scope
- constraints
- stop_conditions
- quality_bar
- priority
- allowed_actions
- forbidden_actions

Rules:
- one objective per episode
- objective may be refined, not silently replaced
- any replacement must be logged as a new objective revision

---

## 2. Task contract

A task is the smallest unit given to an agent.

Fields:
- task_id
- parent_objective_id
- parent_task_id
- role
- goal
- why
- inputs
- expected_output
- acceptance_criteria
- failure_criteria
- budget
- allowed_tools
- dependencies

Rules:
- task must be bounded
- task must be verifiable
- task must not depend on hidden context
- task must not own global state

---

## 3. Result contract

Every returned result must be machine-readable.

Fields:
- task_id
- status
- result_summary
- evidence_refs
- files_changed
- tests_run
- failures
- uncertainties
- recommendation
- confidence

Rules:
- no naked “done”
- confidence is not evidence
- result without evidence is incomplete

---

## 4. Evidence contract

A material claim is acceptable only with evidence.

Fields:
- claim
- evidence_type
- source
- artifact_paths
- timestamp
- reproducibility_notes
- limitations
- confidence

Evidence types:
- test output
- log output
- metric delta
- dataset hash
- artifact hash
- reproduction command
- comparison result

Rules:
- evidence must be reproducible
- evidence must be versioned
- evidence must reference the exact artifact or command
- evidence expires if the underlying state changes

---

## 5. State contract

Canonical state must record:
- current objective
- active episode
- budgets
- task tree
- evidence ledger
- decision ledger
- failure ledger
- last verified snapshot

Rules:
- workers read snapshots, not mutable truth
- Hermes writes canonical state
- derived views must declare they are derived

---

## 6. Verification contract

A result can be accepted only when:
- acceptance criteria are satisfied
- evidence is current
- no conflicting evidence remains unresolved
- independent check passes

If any of these fail, the result is `UNRESOLVED` or `REJECTED`.

---

## 7. Budget contract

Every episode and task must carry a budget:
- tokens
- time
- tool calls
- parallel workers
- retries

Budget exhaustion is a normal stop condition.

---

## 8. Escalation contract

Escalate when:
- evidence conflicts
- state is stale
- tasks repeat without progress
- confidence remains low after bounded attempts
- a safety boundary may be crossed

Escalation means: Hermes re-evaluates the objective and either replans or stops.

---

## 9. Meta-review contract

After repeated failure, Hermes must ask:
- are we solving the right problem?
- is the decomposition wrong?
- is the evidence weak?
- is the model allocation wrong?
- should the episode be killed?

Meta-review is mandatory after repeated no-progress cycles.

---

## 10. Acceptance rule

PASS requires all of the following:
- explicit acceptance criteria
- direct evidence
- fresh state
- no unresolved contradictions
- Hermes approval

Independent agent agreement is never enough by itself.

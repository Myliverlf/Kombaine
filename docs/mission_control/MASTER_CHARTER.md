# HERMES MISSION CONTROL — MASTER DEVELOPMENT CHARTER
## Long-Term Architecture, Evolution Plan and Control Protocol

**Version:** 1.0  
**Date:** 2026-08-29  
**Status:** MASTER GOVERNING DOCUMENT  
**Project:** Trading Combiner / Hermes Mission Control  
**Role:** Long-term architectural north star and governance layer

---

# 0. WHY THIS DOCUMENT EXISTS

This is the persistent master document for the long-term development of the Trading Combiner and Hermes Mission Control.

It exists so that project direction does not depend on:

- one chat session;
- one model context window;
- one agent's temporary memory;
- scattered prompts;
- undocumented assumptions;
- a particular human remembering every previous architectural discussion.

This document defines:

1. what we are building;
2. why we are building it;
3. the architectural principles;
4. the intended long-term system;
5. the development sequence;
6. the role of Hermes and specialized agents;
7. the knowledge/memory architecture;
8. the experiment/research architecture;
9. safety boundaries;
10. how future Iteration files relate to this master plan.

This document is NOT permission to implement every feature described below.

It is a **constitution + north star + roadmap**.

Implementation authority is granted only by explicit bounded Iteration documents.

---

# 1. DOCUMENT HIERARCHY

Future agents must interpret project documents using this hierarchy.

## Level 1 — MASTER GOVERNANCE

```text
HERMES_MISSION_CONTROL_MASTER_CHARTER.md
```

Defines long-term direction, principles, governance and development sequence.

It answers:

> Where are we going and under what rules?

---

## Level 2 — CURRENT SYSTEM TRUTH

```text
COMBINE_SYSTEM_ARCHITECTURE.md
```

Defines the currently known architecture, factual implementation state, gaps and existing safety constraints.

It answers:

> What system do we actually have right now?

Runtime/code/broker evidence can supersede stale factual statements in this document, but contradictions must be documented.

---

## Level 3 — ACCEPTED ARCHITECTURAL DECISIONS

```text
docs/mission_control/decisions/ADR-*.md
```

ADRs record material decisions already accepted.

They answer:

> Why was this architectural choice made?

A later accepted ADR may refine the master plan when evidence requires it.

---

## Level 4 — MISSION CONTROL STATE

Examples:

```text
docs/mission_control/01_SYSTEM_MAP.md
docs/mission_control/02_SOURCE_OF_TRUTH.md
docs/mission_control/08_TECH_DEBT_REGISTER.md
docs/mission_control/09_ROADMAP.md
docs/mission_control/10_MATURITY_MODEL.md
```

These represent the evolving operational understanding of the project.

---

## Level 5 — ITERATION DIRECTIVES

Examples:

```text
HERMES_MISSION_CONTROL_01_*.md
HERMES_MISSION_CONTROL_02_*.md
HERMES_MISSION_CONTROL_03_*.md
...
```

Each Iteration authorizes exactly one bounded development step.

It answers:

> What are we allowed to do now?

An Iteration directive may narrow the master roadmap but must not silently redefine it.

---

## Level 6 — EVIDENCE

Examples:

```text
docs/mission_control/reviews/ITERATION-XX/
research run bundles
tests
runtime verification
logs
broker read-only evidence
```

Evidence answers:

> What can we actually prove?

When design and evidence disagree, do not hide the disagreement.

---

# 2. THE PROJECT WE ARE BUILDING

The final target is NOT simply:

> a trading bot that searches for profitable strategies.

The target is:

> a governed, evidence-driven, self-improving trading research and execution platform.

The platform must eventually be capable of:

```text
MARKET DATA
    ↓
RESEARCH
    ↓
EXPERIMENT PLANNING
    ↓
BACKTEST / VALIDATION
    ↓
KNOWLEDGE EXTRACTION
    ↓
CANDIDATE SELECTION
    ↓
WATCHLIST
    ↓
SIGNAL
    ↓
RISK
    ↓
EXECUTION
    ↓
BROKER
    ↓
POSITION MANAGEMENT
    ↓
RESULT
    ↓
ANALYTICS
    ↓
LEARNING
    ↓
NEXT RESEARCH PLAN
```

Above this trading pipeline sits:

```text
HERMES MISSION CONTROL
```

Mission Control manages the evolution of the entire platform.

---

# 3. THE THREE SYSTEMS

Think of the future architecture as three connected systems.

## SYSTEM A — TRADING FACTORY

Responsible for:

- market data;
- strategies;
- backtests;
- candidates;
- signals;
- risk;
- execution;
- positions;
- analytics.

This is the existing Trading Combiner core.

---

## SYSTEM B — RESEARCH INTELLIGENCE

Responsible for:

- remembering previous experiments;
- preventing useless repetition;
- deciding what should be retested;
- identifying genuinely novel experiments;
- comparing strategy families;
- detecting duplicates/correlation;
- extracting durable conclusions;
- allocating research resources;
- building future experiment plans.

This layer transforms brute-force research into cumulative research intelligence.

---

## SYSTEM C — MISSION CONTROL

Responsible for:

- system architecture;
- roadmap;
- agent coordination;
- technical debt;
- evidence requirements;
- maturity;
- architectural decisions;
- reviews;
- postmortems;
- change control;
- deciding what subsystem should be improved next.

Mission Control does not trade.

Mission Control governs how the trading/research platform evolves.

---

# 4. HUMAN ROLE

The human owner should progressively move away from operating individual scripts.

The target human role is:

```text
OWNER
STRATEGIST
RISK AUTHORITY
ARCHITECTURAL APPROVER
FINAL DECISION MAKER
```

The human defines:

- objectives;
- acceptable risk;
- capital policy;
- major scope;
- strategic direction;
- live-trading authorization;
- high-impact architectural decisions.

The system should increasingly handle:

- investigation;
- experiment planning;
- testing;
- evidence collection;
- implementation of bounded approved tasks;
- reporting;
- monitoring;
- knowledge maintenance.

The human should not become a servant of the automation.

Automation should reduce the human's operational burden.

---

# 5. HERMES ROLE

Hermes is the coordinator of system evolution.

Hermes should eventually be capable of:

```text
observe system
→ identify bottleneck
→ collect evidence
→ request specialist analysis
→ compare recommendations
→ propose bounded change
→ implement after authorization
→ test
→ verify
→ document
→ update knowledge
→ recommend next step
```

Hermes must distinguish:

```text
FACT
DESIGN
HYPOTHESIS
PROPOSAL
DECISION
IMPLEMENTATION
VERIFIED RESULT
```

Hermes must never convert a hypothesis into a fact merely because an agent said it confidently.

---

# 6. SPECIALIZED AGENT COUNCIL

Mission Control may use specialized agents such as:

- Researcher;
- Trading Methodology Analyst;
- System Architect;
- Data Engineer;
- API/Execution Engineer;
- Risk/Safety Reviewer;
- Reliability Engineer;
- Tester;
- Debug Engineer;
- Code Reviewer;
- Performance Analyst;
- Devil's Advocate.

For important architectural decisions, Hermes should gather multiple perspectives.

The purpose is not artificial voting.

The purpose is independent criticism.

A disagreement between agents is useful evidence and must not be silently averaged away.

---

# 7. CORE ENGINEERING LOOP

All important changes should follow:

```text
OBSERVE
↓
MAP CURRENT REALITY
↓
IDENTIFY BOTTLENECK
↓
COLLECT EVIDENCE
↓
PROPOSE OPTIONS
↓
REVIEW
↓
AUTHORIZE
↓
IMPLEMENT MINIMAL CHANGE
↓
TEST
↓
RUNTIME VERIFY
↓
DOCUMENT
↓
UPDATE KNOWLEDGE
↓
SELECT NEXT BOTTLENECK
```

The metric is not:

> lines of code written.

The metric is:

> verified system improvement per unit of complexity introduced.

---

# 8. NON-NEGOTIABLE TRADING SAFETY

The following principles are architectural invariants.

```text
SIGNAL ≠ ORDER
ORDER INTENT ≠ BROKER FILL
LOCAL SLOT ≠ BROKER POSITION
SYNTHETIC PNL ≠ REAL PNL
```

The protected execution chain is:

```text
SIGNAL
→ RISK
→ EXECUTION
→ BROKER
→ CONFIRMATION
→ POSITION
→ ANALYTICS
→ RECONCILIATION
```

Broker reality is authoritative for:

- real positions;
- fills;
- orders;
- operations;
- money;
- commissions.

No research feature is allowed to weaken execution safety.

No Mission Control iteration implicitly grants live-trading permission.

---

# 9. WHY DAILY BRUTE-FORCE RESEARCH IS NOT ENOUGH

A naive system can run thousands of backtests every day.

That creates activity, not necessarily intelligence.

Without memory, after weeks/months the system may:

- retest the same configuration;
- rediscover the same failed idea;
- produce thousands of near-identical parameter variants;
- accumulate huge artifact directories;
- mistake repeated tests for progress;
- overfit by repeatedly mining the same history;
- waste compute;
- pollute the candidate registry;
- make it difficult to know what is genuinely new.

Therefore the project must evolve from:

```text
MORE TESTS
```

to:

```text
BETTER EXPERIMENT SELECTION
+
CUMULATIVE KNOWLEDGE
```

---

# 10. EXPERIMENT IDENTITY

Research Intelligence must eventually give every experiment a stable identity.

Conceptually:

```text
EXPERIMENT_ID =
hash(
    instrument identity
    + timeframe
    + strategy family
    + strategy implementation version
    + normalized parameters
    + dataset identity/hash
    + historical horizon
    + cost model
    + sizing model
    + backtest engine version
    + relevant validation policy
)
```

Exact implementation must be designed from repository evidence.

The goal is to answer:

> Have we already tested the same thing under materially equivalent conditions?

---

# 11. EXPERIMENT CLASSIFICATION

Before spending compute, a planned experiment should eventually be classified as:

```text
NEW
REPRODUCTION
REVALIDATION
DUPLICATE
SUPERSEDED
LOW_INFORMATION_VALUE
```

## NEW

A materially new hypothesis/configuration.

## REPRODUCTION

Intentional repeat to verify reproducibility.

## REVALIDATION

Retest justified by changed conditions.

Examples:

- enough new market data;
- changed regime;
- new cost assumptions;
- code version changed;
- data quality changed;
- scheduled robustness check.

## DUPLICATE

No meaningful new information expected.

Normally skip.

## SUPERSEDED

An older experiment whose assumptions/code/data have been replaced.

Retain history but do not treat as current evidence.

## LOW_INFORMATION_VALUE

Technically new but too similar to existing experiments to justify compute.

---

# 12. EXPERIMENT MEMORY

The future system needs an Experiment Memory.

It should allow queries such as:

```text
Have we tested this exact configuration?
What nearby parameter regions were tested?
Why did this strategy family fail?
When was it last revalidated?
What data version was used?
Did it survive costs?
Did it survive multiple windows?
Did it survive recent tail data?
Which experiment supersedes this one?
```

Experiment Memory must not become an uncontrolled giant JSON file.

Use appropriate indexed storage once requirements are proven.

Raw artifacts and queryable metadata may use different storage.

---

# 13. KNOWLEDGE IS NOT THE SAME AS EXPERIMENT HISTORY

Experiment history answers:

> What happened in individual runs?

Knowledge answers:

> What have we learned from many runs?

The system should periodically distill experiments into higher-level findings.

Examples:

```text
VWAP-band family unstable on instrument X after costs.

ATR breakout performs only in high-volatility regimes.

Parameter region A is robust across windows.

Parameter region B is a narrow overfit island.

Strategies C and D are strongly redundant.

Ticker/timeframe E lacks sufficient trustworthy history.

Recent market regime invalidated previously strong candidate F.
```

Every knowledge claim must retain provenance back to supporting experiments.

---

# 14. KNOWLEDGE CONFIDENCE

Knowledge should not be binary.

A future knowledge object may include:

```text
claim
confidence
supporting experiment IDs
contradicting experiment IDs
last validated
market regime
data/code versions
status
```

Possible statuses:

```text
TENTATIVE
SUPPORTED
STRONG
CONTRADICTED
STALE
SUPERSEDED
```

This prevents old conclusions from becoming permanent dogma.

---

# 15. RESEARCH PLANNER

Once Experiment Memory exists, research should be planned instead of blindly enumerated.

The planner should allocate a research budget among categories such as:

```text
EXPLOIT
EXPLORE
REVALIDATE
REPRODUCE
STRESS TEST
```

## EXPLOIT

Search near proven robust regions.

## EXPLORE

Test genuinely different hypotheses.

## REVALIDATE

Check whether previously good strategies remain valid.

## REPRODUCE

Verify important findings.

## STRESS TEST

Try to break candidates through:

- alternative windows;
- costs;
- slippage;
- parameter perturbation;
- regime splits;
- tail periods;
- walk-forward.

This creates purposeful research rather than raw parameter spam.

---

# 16. NOVELTY GATE

Before research execution, Mission Control / Research Intelligence should eventually ask:

```text
Is this experiment informative enough to run?
```

Novelty may consider:

- exact identity;
- parameter distance;
- strategy-family similarity;
- dataset difference;
- new data accumulated;
- regime change;
- previous confidence;
- uncertainty;
- expected information gain.

The novelty gate must not prevent legitimate scheduled revalidation.

Its purpose is to stop useless repetition, not freeze learning.

---

# 17. STRATEGY LIFECYCLE

Strategies/candidates should have explicit lifecycle states.

Conceptually:

```text
IDEA
↓
RESEARCH
↓
VALIDATED_CANDIDATE
↓
PAPER_CANDIDATE
↓
WATCHLIST
↓
ACTIVE_SIGNAL_POOL
↓
ACTIVE / ELIGIBLE
↓
DEGRADED
↓
REVALIDATION_REQUIRED
↓
ROTATED_OUT
↓
ARCHIVED
```

Exact names should align with the canonical registry.

A strategy should not remain active forever because it was once profitable.

---

# 18. STRATEGY DECAY

Research Intelligence should detect when evidence decays.

Potential indicators:

- recent tail failure;
- falling PF;
- increased drawdown;
- regime mismatch;
- execution costs changed;
- strategy correlation increased;
- signal frequency collapsed;
- data assumptions invalidated.

Decay should trigger:

```text
REVIEW
REVALIDATION
DEGRADATION
ROTATION PROPOSAL
```

not uncontrolled automatic trading changes.

---

# 19. CORRELATION / DUPLICATE STRATEGY CONTROL

Ten profitable strategies are not necessarily ten independent strategies.

The future selection layer should detect:

- highly correlated equity curves;
- similar entries/exits;
- same underlying factor;
- parameter clones;
- same risk exposure.

Candidate selection should optimize portfolio diversity, not just isolated backtest rank.

---

# 20. IMMUTABLE RESEARCH RUNS

Every important research run must be reproducible.

Target structure remains conceptually:

```text
runs/<run-id>/
├── manifest
├── research plan
├── candidate ledger
├── eligible candidates
├── selected/top candidates
├── report
├── charts
└── checks
```

A run must record enough information to reproduce:

- data;
- code;
- parameters;
- assumptions;
- costs;
- universe;
- timing;
- results.

Global `latest` should only point atomically to a completed run.

Partial runs must not masquerade as completed truth.

---

# 21. RESEARCH ARTIFACT RETENTION

Not every byte should live forever in the hot path.

Define deterministic retention classes.

Conceptually:

## HOT

Recent and active evidence.

## WARM

Important historical evidence used for comparison/revalidation.

## COLD

Archived reproducibility evidence.

## SUMMARIZED

Raw low-value detail removed only after required knowledge/metrics are safely retained, according to explicit policy.

Never destroy evidence needed to explain real trading decisions.

Retention policy must be deterministic and documented.

---

# 22. CANONICAL SOURCES OF TRUTH

Every domain needs exactly one canonical authority.

Examples:

```text
real broker state
    → broker API

strategy lifecycle
    → canonical strategy registry

research evidence
    → immutable research run bundle

experiment metadata
    → future Experiment Memory

architectural decisions
    → accepted ADRs

technical debt
    → Mission Control debt register

current architecture map
    → Mission Control system map

local analytics
    → one canonical analytics database
```

Legacy representations may be derived but must not compete.

---

# 23. OBSERVABILITY

A mature system must explain itself.

For any real trade, we eventually want:

```text
research provenance
→ candidate selection
→ watchlist admission
→ signal
→ risk verdict
→ order intent
→ broker order
→ fill
→ position
→ exit
→ PnL
→ post-trade evaluation
```

For any research candidate:

```text
why tested
→ what was tested
→ what evidence resulted
→ why accepted/rejected
→ whether it was novel
→ when it should be reconsidered
```

For any code change:

```text
why changed
→ evidence
→ tests
→ runtime verification
→ ADR if material
→ rollback
```

---

# 24. TECHNICAL DEBT AS FIRST-CLASS STATE

Technical debt must not live in chat messages.

Each item should include:

```text
ID
subsystem
description
evidence
severity
probability
impact
workaround
proposed resolution
dependencies
status
```

Mission Control should use debt when selecting future work.

Not all debt should be fixed immediately.

Prioritize by risk and leverage.

---

# 25. MATURITY MODEL

Each subsystem should be scored approximately:

```text
0 — UNKNOWN
1 — FRAGILE
2 — REPEATABLE
3 — CONTROLLED
4 — OBSERVABLE / RECOVERABLE
5 — EVIDENCE-BACKED PRODUCTION QUALITY
```

Score at least:

- DATA;
- RESEARCH;
- BACKTEST;
- SELECTION;
- WATCHLIST;
- SIGNAL;
- RISK;
- EXECUTION;
- BROKER RECONCILIATION;
- POSITION MANAGEMENT;
- ANALYTICS;
- REPORTING;
- EXPERIMENT MEMORY;
- KNOWLEDGE;
- AGENT ORCHESTRATION;
- OPERATIONS/SCHEDULING.

Scores require evidence.

Mission Control should seek balanced maturity.

A level-5 research engine sitting on level-1 execution is not a mature trading platform.

---

# 26. MISSION CONTROL SELF-REVIEW

Mission Control should eventually run a periodic review.

Questions:

1. What objectively improved?
2. What objectively regressed?
3. Which assumptions were disproven?
4. What new evidence appeared?
5. What technical debt increased?
6. What technical debt decreased?
7. Where was work duplicated?
8. Where was compute wasted?
9. Which subsystem is now the bottleneck?
10. What change has highest expected leverage?
11. Did any safety boundary weaken?
12. What requires human approval?

This is not motivational text.

Every important claim needs evidence.

---

# 27. POSTMORTEMS

Failures should become durable knowledge.

Create postmortems for events such as:

- unintended broker behavior;
- duplicate order;
- wrong universe admission;
- stale research promoted;
- incorrect report;
- data contamination;
- reconciliation failure;
- corrupted state;
- scheduler collision;
- significant research methodology error.

Postmortems should answer:

```text
what happened
why
impact
detection
root cause
contributing factors
fix
prevention
new test/invariant
```

No blame language.

The purpose is system learning.

---

# 28. CHANGE RISK CLASSES

Future changes should be classified.

## CLASS 0 — Documentation only

No runtime effect.

## CLASS 1 — Research-only

Cannot affect execution or broker state.

## CLASS 2 — Runtime non-trading

Operational change with no broker action.

## CLASS 3 — Trading-path safety

Touches signal/risk/execution/position logic but is not intended to change strategy semantics.

Requires stronger tests and runtime proof.

## CLASS 4 — Trading policy

Changes:

- strategy semantics;
- risk limits;
- sizing;
- auto-swap;
- live policy;
- broker behavior.

Requires explicit human approval.

## CLASS 5 — Capital-critical

Live activation or changes capable of materially changing real capital exposure.

Requires explicit dedicated approval and readiness review.

An Iteration must state its class.

---

# 29. DEVELOPMENT PHASES — MASTER ROADMAP

The project should progress through the following major phases.

These phases define sequence, not automatic authorization.

---

## PHASE 0 — GOVERNANCE FOUNDATION

Goal:

> Stop developing from scattered chat memory.

Build:

- Constitution;
- System Map;
- Source-of-Truth registry;
- Tech Debt register;
- Roadmap;
- Maturity Model;
- evidence structure.

Status:

> Started/completed through Mission Control Iteration 0; keep maintained.

---

## PHASE 1 — P0 SAFETY & EXECUTION TRUTH

Goal:

> Make critical trading boundaries provable before adding intelligence.

Includes:

- canonical universe admission;
- swap/pending safety;
- signal → risk → execution proof;
- paper/live hard guard;
- idempotency;
- duplicate-order protection;
- broker/local truth;
- reconciliation;
- analytics truth;
- crash/restart behavior.

Iterations already started:

```text
Iteration 01 — Universe Gate
Iteration 02 — Execution Truth
```

Do not skip unresolved P0 defects.

---

## PHASE 2 — REPRODUCIBLE RESEARCH FOUNDATION

Goal:

> Every research result must be reproducible and attributable.

Build/verify:

- immutable run bundles;
- run lock;
- exact run-id handoff;
- complete candidate ledger;
- deterministic research plan;
- explicit universe;
- dataset manifests/hashes;
- code version;
- cost/slippage assumptions;
- complete/partial/blocked status;
- atomic completed-run pointer;
- canonical seeder handoff from completed eligible candidates.

This phase removes the current mixed/legacy research pipeline.

---

## PHASE 3 — EXPERIMENT MEMORY

Goal:

> Stop wasting research on things already known.

Build:

- experiment identity;
- metadata index;
- duplicate detection;
- revalidation rules;
- supersession;
- query API;
- experiment provenance.

Before running a planned experiment, the system should know whether it is:

```text
NEW
REPRODUCTION
REVALIDATION
DUPLICATE
SUPERSEDED
LOW_INFORMATION_VALUE
```

---

## PHASE 4 — RESEARCH INTELLIGENCE / NOVELTY

Goal:

> Make research selective and informative.

Build:

- novelty gate;
- parameter-space similarity;
- strategy-family similarity;
- research budget;
- explore/exploit/revalidate allocation;
- uncertainty-aware planning;
- low-information experiment suppression.

At this stage, "10,000 tests per day" is no longer the primary goal.

The goal becomes:

> maximum useful information per compute budget.

---

## PHASE 5 — KNOWLEDGE LAYER

Goal:

> Convert millions of experiment facts into durable understanding.

Build:

- knowledge objects;
- confidence;
- supporting/contradicting evidence;
- stale/superseded knowledge;
- strategy-family findings;
- regime findings;
- data-quality findings;
- failure-pattern memory.

Mission Control and Research Planner should consume knowledge, not raw experiment piles.

---

## PHASE 6 — STRATEGY LIFECYCLE & PORTFOLIO INTELLIGENCE

Goal:

> Manage strategies as evolving assets.

Build:

- explicit lifecycle;
- decay detection;
- scheduled revalidation;
- correlation/redundancy control;
- diversity-aware candidate selection;
- rotation recommendations;
- evidence-backed degradation.

Research must not automatically close live positions.

Trading policy remains separately governed.

---

## PHASE 7 — SYSTEM MODULARITY & OPERABILITY

Goal:

> Make components replaceable and recoverable.

Improve:

- explicit interfaces;
- dependency boundaries;
- scheduler coordination;
- locks;
- atomic writes;
- configuration ownership;
- observability;
- health checks;
- recovery procedures;
- backup/restore;
- deterministic deployments.

Remove legacy only after replacement is proven.

---

## PHASE 8 — ARCHITECTURAL COUNCIL

Goal:

> Important changes receive independent criticism.

Hermes orchestrates specialist reviews.

Material proposals may receive:

- architecture review;
- trading methodology review;
- safety review;
- data review;
- testing review;
- devil's-advocate review.

Hermes synthesizes disagreements and sends the decision packet to the human when approval is required.

---

## PHASE 9 — MISSION CONTROL AUTONOMOUS PROJECT MANAGEMENT

Goal:

> Hermes can manage routine evolution without losing governance.

Hermes may:

- maintain roadmap;
- update maturity;
- maintain debt;
- detect bottlenecks;
- propose next Iteration;
- assign agents;
- collect evidence;
- detect regressions;
- generate periodic reviews.

Hermes may NOT self-authorize Class 4/5 changes.

---

## PHASE 10 — CONTROLLED SELF-IMPROVEMENT

Goal:

> The platform improves how it improves.

Possible capabilities:

- evaluate which research methods produce useful candidates;
- detect wasted compute;
- compare agent performance;
- improve experiment planning;
- improve test coverage;
- identify recurring defect classes;
- recommend architectural simplification;
- update knowledge confidence.

Self-improvement must remain:

```text
BOUNDED
AUDITABLE
REVERSIBLE
EVIDENCE-BASED
```

It must never mean unrestricted self-modifying live trading code.

---

# 30. WHAT “SELF-IMPROVING” MEANS HERE

Self-improvement does NOT mean:

> an AI changes everything by itself.

It means the system becomes better at:

- choosing what to investigate;
- remembering previous results;
- detecting repeated mistakes;
- allocating compute;
- testing hypotheses;
- identifying uncertainty;
- maintaining documentation;
- finding architectural bottlenecks;
- proposing evidence-backed changes.

The human remains the authority for high-impact trading decisions.

---

# 31. HOW FUTURE ITERATION FILES MUST WORK

Every future Iteration file should begin with:

```text
MASTER CHARTER:
HERMES_MISSION_CONTROL_MASTER_CHARTER.md

CURRENT SYSTEM BASELINE:
COMBINE_SYSTEM_ARCHITECTURE.md

PREVIOUS MISSION CONTROL STATE:
docs/mission_control/*
```

Then state:

```text
ITERATION NUMBER
CHANGE CLASS
OBJECTIVE
SCOPE
NON-GOALS
DISCOVERY REQUIRED
IMPLEMENTATION AUTHORIZATION
TEST REQUIREMENTS
RUNTIME VERIFICATION
DOCUMENTATION UPDATES
DEFINITION OF DONE
STOP CONDITIONS
FINAL REPORT FORMAT
```

Every Iteration ends with:

> STOP. Do not automatically begin the next roadmap task.

This preserves human control.

---

# 32. FUTURE ITERATION PROMPT TEMPLATE

Use the following conceptual template.

```text
You are Hermes acting as Mission Control.

Read the Master Charter, current architecture baseline,
accepted ADRs, Mission Control state and previous iteration evidence.

Execute ONLY this Iteration.

First verify current reality.
Do not assume the roadmap description equals implementation reality.

Perform:
DISCOVERY
→ DESIGN
→ TEST
→ MINIMAL IMPLEMENTATION
→ REGRESSION TEST
→ SAFE RUNTIME VERIFICATION
→ DOCUMENTATION
→ FINAL REPORT

Do not broaden scope.

Do not modify Class 4/5 trading policy without explicit human approval.

If evidence contradicts the Master Charter, preserve the evidence,
record the contradiction and propose a charter/ADR update rather than
silently forcing reality to match the document.

At completion, recommend exactly one next bounded task and STOP.
```

Future Iteration documents should contain the actual detailed task prompt, not require a separate chat prompt.

---

# 33. MASTER CHARTER CHANGE PROTOCOL

This document is durable but not immutable dogma.

It may change when:

- runtime evidence disproves an assumption;
- architecture evolves;
- a better design is accepted;
- an ADR changes a major boundary;
- project goals change.

However, Hermes must not silently rewrite this file.

Material changes require:

1. proposed diff;
2. reason;
3. evidence;
4. impact;
5. human approval when strategic;
6. version increment;
7. changelog entry.

---

# 34. CONTEXT RECOVERY PROTOCOL

Whenever a new agent/session/model begins work and prior conversational context may be missing, it should recover context from documents in this order:

```text
1. MASTER CHARTER
2. CURRENT COMBINE ARCHITECTURE
3. MISSION CONTROL SYSTEM MAP
4. SOURCE-OF-TRUTH MAP
5. ROADMAP
6. MATURITY MODEL
7. TECH DEBT
8. ACCEPTED ADRs relevant to task
9. LAST COMPLETED ITERATION REPORT
10. CURRENT ITERATION DIRECTIVE
```

The project must be recoverable from repository evidence without requiring old chat logs.

This is a core architectural requirement.

---

# 35. ANTI-CONTEXT-LOSS RULE

Important decisions made in chat are temporary until written into the project.

If a conversation produces a durable architectural conclusion, convert it into one of:

```text
Master Charter update
ADR
System Map update
Source-of-Truth update
Roadmap update
Tech Debt entry
Knowledge object
Iteration directive
Postmortem
```

Chat is a working surface.

The repository is project memory.

---

# 36. ANTI-DOCUMENTATION-ROT RULE

Documentation itself can become legacy.

Therefore every major document should have:

```text
owner/domain
status
version/date
source evidence
last verified date where relevant
```

Mission Control should detect contradictions between docs and runtime.

Never trust documentation solely because it exists.

---

# 37. DEFINITION OF A HEALTHY FUTURE PLATFORM

The platform is approaching the intended architecture when:

- critical execution boundaries are proven;
- broker truth always dominates local guesses;
- research runs are reproducible;
- candidate handoff is canonical;
- experiments have stable identity;
- useless duplicate experiments are suppressed;
- revalidation happens for explicit reasons;
- knowledge is distilled from experiment history;
- strategy decay is detected;
- candidate diversity is measured;
- every real trade has provenance;
- every important architectural change has evidence;
- technical debt is visible;
- system maturity is measurable;
- agents work from the same project memory;
- a new model/session can recover project state from repository documents;
- Hermes can recommend the next high-leverage change without inventing context;
- human approval remains required for capital-critical policy.

---

# 38. WHAT SUCCESS LOOKS LIKE

The desired long-term operating model is:

```text
HUMAN:
"Here are the objectives and risk boundaries."

        ↓

MISSION CONTROL:
"Here is the current state, bottleneck, evidence and proposed next change."

        ↓

SPECIALIST AGENTS:
investigate / design / implement / test

        ↓

MISSION CONTROL:
verifies evidence and updates project memory

        ↓

TRADING / RESEARCH PLATFORM:
operates under explicit boundaries

        ↓

RESEARCH INTELLIGENCE:
learns from experiments and proposes better research

        ↓

HUMAN:
approves major strategic/risk decisions
```

The system should become more capable over time **without becoming less understandable**.

---

# 39. BOOTSTRAP INSTRUCTION FOR HERMES

When this Master Charter is first introduced into the repository, perform ONLY the following bootstrap task.

## Task

1. Read this document completely.
2. Read `COMBINE_SYSTEM_ARCHITECTURE.md`.
3. Read the current `docs/mission_control/` documents.
4. Read completed Iteration evidence currently available.
5. Determine the canonical repository location for this Master Charter.
6. Store/copy it there if it is not already stored.
7. Add a reference to this Master Charter from `00_CONSTITUTION.md`.
8. Add the document hierarchy from Section 1 to Mission Control documentation where appropriate.
9. Add the Context Recovery Protocol from Section 34 to the appropriate Mission Control governance document.
10. Do not alter trading code.
11. Do not begin any roadmap phase merely because it is described here.
12. If another Iteration is currently in progress, do not interrupt or modify its runtime work. Integrate this charter as governance only.
13. Return a short report showing:
    - canonical charter path;
    - documents updated;
    - any contradiction discovered;
    - current active Iteration;
    - confirmation that no trading behavior changed.

Then STOP.

---

# 40. STANDING INSTRUCTION FOR ALL FUTURE WORK

Before executing any future Mission Control Iteration:

> Read and obey this Master Charter as the long-term governing context.

But remember:

> The Master Charter defines direction.  
> Evidence defines reality.  
> ADRs define accepted architectural decisions.  
> Iteration files define current implementation authority.  
> The human defines high-impact strategic and capital-risk permission.

Never collapse these layers into one.

---

# 41. FINAL NORTH STAR

We are not trying to build the system that runs the largest number of backtests.

We are trying to build a system that **learns the most from each justified experiment**.

We are not trying to build an agent that writes the most code.

We are trying to build an engineering organization in software form that **makes the highest-quality verified changes**.

We are not trying to remove the human.

We are trying to move the human upward:

```text
FROM:
operator

TO:
owner / strategist / risk authority
```

The final goal is a Trading Combiner whose:

```text
execution is safe,
research is reproducible,
experiments accumulate knowledge,
strategies are continuously re-evaluated,
architecture remains understandable,
agents share persistent project memory,
and evolution is governed by evidence.
```

That is the long-term architecture of Hermes Mission Control.

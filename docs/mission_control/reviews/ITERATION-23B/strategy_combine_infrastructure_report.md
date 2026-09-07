# Strategy Combine — Infrastructure Report

**Audience:** Architect / owner handoff
**Scope:** actual `strategy_combine` contour only
**Status basis:** verified Mission Control + broker truth + Iteration 23B findings
**Mode:** paper
**paper_first:** true
**LIVE_EXECUTE:** denied
**LIVE_CANCEL:** denied

---

## 0. Purpose

This document is the current end-to-end infrastructure map for `strategy_combine`:
- what exists;
- what talks to what;
- which artifacts are canonical;
- which pieces are derived or legacy;
- where the verified broker truth and live-risk policy landed;
- what remains blocked.

It is written for an architect review: practical, factual, and tied to the current verified state.

---

## 1. Executive summary

Current state:
- the research / selection / risk / execution stack exists and is decomposed into distinct layers;
- the broker can be queried read-only and the account is no longer UNKNOWN;
- the owner’s capital intent is now encoded as a versioned policy;
- the system remains **NOT_READY** for controlled live because no live-eligible strategy exists yet.

Key facts:
- **Broker truth:** proven read-only
- **Account state:** NON_FLAT
- **Reconciliation:** DEGRADED
- **Live policy:** `LIVE_RISK_V1` exists
- **Pilot strategy:** `NO_LIVE_STRATEGY_ELIGIBLE`
- **Final readiness:** `NOT_READY`

---

## 2. Authority model and source-of-truth hierarchy

The system uses strict authority ordering.

### 2.1 Broker / position truth
Highest factual authority for real money and positions:
- Tinkoff broker API
- broker operations / fills

Rule:
- broker wins over local state, reports, dashboards, and derived views.

### 2.2 Local operational state
Used for runtime decisions and slot management:
- `state/portfolio.json`

Rule:
- this is operational state, not truth.
- if it disagrees with broker truth, broker wins.

### 2.3 Strategy registry
Canonical lifecycle state for strategies:
- `state/strategy_registry.json`

Rule:
- registry is authoritative for strategy lifecycle.
- derived views such as waitlist / signal pool must never override registry.

### 2.4 Derived compatibility views
- `state/waitlist.json`
- `state/signal_pool.json`

Rule:
- these are legacy / compatibility exports only.
- they are not source-of-truth.

### 2.5 Analytics and evidence stores
- root `analytics.db` is canonical local analytics DB
- `state/analytics.db` is empty / legacy / conflicting and is not authoritative
- report artifacts are presentation unless tied to immutable evidence

### 2.6 Governance / control-plane sources
- `state/mission_control.db`
- `state/human_review.db`
- `state/research_knowledge.db`
- `state/market_regimes.db`
- `state/replacement_ranking.db`

These are read-only or control-plane stores. They govern decisions, but they do not mutate broker state.

---

## 3. End-to-end runtime pipeline

### 3.1 Data acquisition
Sources:
- Tinkoff / local OHLCV data
- long-history downloads
- daily archiver
- 15m intraday archiver

Relevant components:
- `tools/long_history_download.py`
- `tools/daily_history_archiver.py`
- `download_15m.sh`
- `combine-15m.timer`

Output:
- CSV history under the futures lab artifact store
- fresh timeframes for research/backtest/certification

### 3.2 Research and backtesting
Components:
- `code/strategy_architect_autopilot.py`
- `futures_lab.run_backtest`
- strategy zoo / candidate scorer

Output:
- candidate metrics
- charts
- run JSON / markdown bundles
- registry candidates

### 3.3 Canonical handoff
Components:
- `core/seeder_handoff.py`
- `core/seeder.py`

Rule:
- completed canonical runs hand off via the validated artifact contract.
- legacy scan path exists only with explicit opt-in.

### 3.4 Strategy registry and views
Components:
- `code/strategy_registry.py`
- `core/registry.py`
- `core/strategy_supervisor_flow.py`

Output:
- `state/strategy_registry.json`
- legacy exports to `state/waitlist.json` and `state/signal_pool.json`

### 3.5 Signal → risk → supervision
Components:
- `core/supervisor.py`
- `core/risk.py`
- `code/strategy_replacement_policy.py`
- `combine-supervisor.timer`

Role:
- evaluate signals against freshness, regime, universe, and risk gates;
- decide slot actions;
- never bypass risk.

### 3.6 Execution and position management
Components:
- `core/engine.py`
- Tinkoff Invest API
- `state/portfolio.json`

Role:
- turn approved operational intent into broker interaction;
- only broker-authorized paths may reach execution;
- in paper mode, no live execution is allowed.

### 3.7 Experiment memory / regime / knowledge / ranking / review / MC
Additional layers exist and are observational / governance-oriented:
- `core/experiment_memory.py`
- `core/market_regime.py`
- `core/research_knowledge.py`
- `core/replacement_ranking.py`
- `core/human_review.py`
- `core/mission_control.py`

They improve decision quality, but none of them may mutate broker state.

### 3.8 Strategy factory / pre-live envelope
Iteration 22+ added the strategy factory and pre-live snapshot layer:
- `core/strategy_factory.py`
- `PreLiveSnapshot`
- `WalkForwardProver`
- `StopKillProcedure`
- `ExplorationPolicy`

Iteration 23B then added the live-risk policy and broker-truth closure.

---

## 4. Canonical modules and what each one does

### 4.1 Data layer
Purpose:
- pull and preserve market history
- keep long-history and intraday evidence fresh enough for research and certification

Bolts / nuts:
- timer cadence
- CSV stores
- history download scripts
- freshness / coverage expectations

### 4.2 Research layer
Purpose:
- generate candidate strategies and run backtests
- produce immutable evidence bundles
- distinguish equivalent, near-equivalent, and novel experiments

Bolts / nuts:
- strategy archetypes
- candidate ledgers
- experiment memory
- regime evidence
- research knowledge

### 4.3 Selection / registry layer
Purpose:
- keep one canonical lifecycle state for strategies
- export derived compatibility views
- prevent duplicated authorities

Bolts / nuts:
- registry entries
- waitlist view
- signal pool view
- handoff validation

### 4.4 Risk / supervision layer
Purpose:
- interpret signals through a risk gate
- decide whether a strategy/slot may proceed
- fail closed on unknown data or ambiguous state

Bolts / nuts:
- universe admission
- freshness gates
- regime gates
- portfolio/slot risk rules
- VETO semantics

### 4.5 Execution layer
Purpose:
- interact with broker only when allowed
- preserve paper/live boundary
- prevent accidental order submission

Bolts / nuts:
- `Engine.post()` boundary
- `mode=paper`
- `paper_first=true`
- broker snapshot semantics
- reconciliation semantics

### 4.6 Governance layer
Purpose:
- preserve human review boundaries
- store control-plane decisions and incidents
- keep bounded evidence and immutable snapshots

Bolts / nuts:
- human review DB
- mission control DB
- ADRs
- scorecards
- readiness gates

---

## 5. Runtime scheduling and timers

Observed / referenced schedule pieces:
- `combine-15m.timer` — intraday candle download
- `combine-supervisor.timer` — supervision tick
- `combine-seeder.timer` — candidate seeding / validation
- Hermes daily history archive job
- daily morning report job
- architect research cycle job

Important principle:
- scheduling exists, but one fully verified locked daily transaction from data → research → registry → report is still not a single end-to-end controlled artifact.

---

## 6. State / artifact map

### Canonical local files
- `config.json`
- `state/portfolio.json`
- `state/strategy_registry.json`
- `state/signal_pool.json`
- `state/waitlist.json`
- `analytics.db`
- `state/mission_control.db`
- `state/human_review.db`
- `state/research_knowledge.db`
- `state/market_regimes.db`
- `state/replacement_ranking.db`
- `state/experiment_memory.db`

### Iteration 23B live-risk artifacts
- `state/live_risk/LIVE_RISK_V1.json`
- `state/broker_truth_snapshot_23b.json`
- `state/prelive_snapshot.json`
- `tests/test_live_preconditions_23b.py`

### Evidence bundle
- `docs/mission_control/reviews/ITERATION-23B/`
- `docs/mission_control/decisions/ADR-2026-08-30-owner-live-risk-policy-and-broker-truth-closure.md`

---

## 7. Verified broker truth and live-risk closure

### 7.1 Broker truth
Verified read-only broker evidence shows:
- account `2042640199`
- broker account name: `Брокерский счёт`
- `get_accounts`: OK
- `get_portfolio`: OK
- `get_orders`: OK
- mutating calls: **0**

### 7.2 Account state
Broker truth shows:
- cash and equity are real
- portfolio contains 4 positions
- the account is **NON_FLAT**
- there is an external `LKOH` position (4 shares)

### 7.3 Live risk policy
`LIVE_RISK_V1` now exists and is authoritative for controlled-live policy.

Main values:
- capital base: `FULL_ACCOUNT_EQUITY`
- max gross exposure: `10%`
- max single position exposure: `10%`
- max risk per trade: `0.25%`
- max open strategy risk: `0.50%`
- max total open risk: `0.50%`
- daily halt: `1.00%`
- weekly halt: `2.00%`
- drawdown halt: `5.00%`
- max concurrent live positions: `1`
- leverage: `NO`
- averaging down: `NO`
- pyramiding: `NO`
- shorting: `NO`

### 7.4 Universe policy
- TIER_1: `GAZP`, `LKOH`, `SBER`
- RESTRICTED: `BR`, `Si`

### 7.5 Strategy eligibility
No live-eligible strategy yet:
- `NO_LIVE_STRATEGY_ELIGIBLE`
- thresholds were not weakened

### 7.6 Readiness
Final readiness remains:
- `NOT_READY`

---

## 8. Readiness logic

Readiness is blocked by G4:
- no live-eligible strategy with enough walk-forward evidence and a defensible risk boundary

Even with broker truth proven and policy created:
- controlled live is still blocked until a real eligible strategy exists

This is deliberate.

---

## 9. Operating constraints

These are the enforced constraints in the current state:
- paper mode only
- `paper_first=true`
- no live mode activation
- no leverage
- no averaging down
- no pyramiding for first pilot
- no automatic liquidation unless a separately governed position rule requires it
- no broker-mutating calls in Iteration 23B
- no human authorization issued

---

## 10. Verified current system status

Current verified state:
- broker truth: **PROVEN**
- account equity: **verified**
- reconciliation: **DEGRADED**
- position state: **NON_FLAT**
- data quality: `GAZP`/`SBER` certified, `LKOH` conditional, `BR`/`Si` blocked
- pilot strategy: **NO_LIVE_STRATEGY_ELIGIBLE**
- final readiness: **NOT_READY**

---

## 11. Open blockers

1. **G4 FAIL** — no live-eligible strategy
2. `LKOH` 1095d duplicate unresolved
3. external broker position exists and is not system-managed
4. no human live authorization issued

---

## 12. What changed in Iteration 23B

### Added
- owner capital policy
- versioned live risk policy
- broker read-only runtime proof
- reconciliation evidence
- broker position state evidence
- data certification evidence
- pilot strategy selection evidence
- pilot allocation proof
- abort conditions
- pre-live snapshot
- readiness scorecard
- tests summary
- ADR
- final report

### Modified
- `state/prelive_snapshot.json`
- `tests/test_live_preconditions.py`

### Not changed
- production trading behavior
- risk thresholds
- mode
- paper_first
- live execution permission

---

## 13. Appendix — Iteration 23B final report

Below is the final report content appended for architecture review continuity.

### A. Executive Result
**NOT_READY**

Broker truth established. Live risk policy codified. Strategy evidence still insufficient.
The system remains blocked by G4 (no eligible strategy).

### B. Human Policy Captured
- `capital_base_policy = FULL_ACCOUNT_EQUITY`
- full account equity (~21,040 RUB) eligible as capital pool
- capital pool ≠ forced exposure
- risk at portfolio AND position levels
- conservative optimization mandate
- never weaken Risk Gate to deploy more capital

### C. LIVE_RISK_V1 Exact Values
- Policy ID: `LIVE_RISK_V1`
- Version: `1.0.0`
- Storage: `state/live_risk/LIVE_RISK_V1.json`
- capital_base_method: `FULL_ACCOUNT_EQUITY`
- max_gross_exposure: `10% of equity`
- max_single_position: `10% of equity`
- max_risk_per_trade: `0.25% of equity`
- max_strategy_risk: `0.50% of equity`
- max_total_risk: `0.50% of equity`
- daily_loss_halt: `1.00% of start-of-day equity`
- weekly_loss_halt: `2.00% of start-of-week equity`
- drawdown_halt: `5.00% from HWM`
- max_concurrent_positions: `1`
- leverage: `NO`
- averaging_down: `NO`
- pyramiding: `NO`
- shorting: `NO`
- immutable: `true`

### D. Tradable Universe
- TIER_1: `GAZP`, `LKOH`, `SBER`
- RESTRICTED: `BR`, `Si`

### E. Broker Read-Only Proof
- SDK imported
- account verified
- portfolio verified
- orders verified
- mutating calls: `0`
- snapshot stored in `state/broker_truth_snapshot_23b.json`

### F. Verified Capital Base
- cash RUB: `20,191.20`
- cash USD: `0.8`
- cash EUR: `0.55`
- LKOH 4 shares
- total equity: `~21,040 RUB`

### G. Reconciliation
**DEGRADED**
- broker account ↔ positions: consistent
- broker positions ↔ local slots: degraded
- no corrective order permitted

### H. Broker Position State
**NON_FLAT**
- LKOH 4 shares
- pre-existing owner position
- do not close

### I. Data Certification
- GAZP: certified
- SBER: certified
- LKOH: conditional
- BR: blocked
- Si: blocked

### J. Pilot Strategy
**NO_LIVE_STRATEGY_ELIGIBLE**

### K. Pilot Allocation
**NO_TRADE**

### L. Risk Hierarchy Proof
```text
GLOBAL SAFETY HALT
→ BROKER TRUTH / RECONCILIATION
→ ACCOUNT / PORTFOLIO RISK LIMITS
→ STRATEGY-SPECIFIC RISK RULES
→ ALLOCATION POLICY
→ SIGNAL
→ EXECUTION
```

### M. Human LIVE Authorization
**NOT ISSUED**

### N. Pre-Live Snapshot
- ID: `snap_23B_20260830T133202`
- hash: `adf68840638efe18`
- mode: `paper`
- paper_first: `true`

### O. G1–G12 Statuses
- G1: PASS
- G2: PASS
- G3: PASS
- G4: FAIL
- G5: PASS
- G6: PASS
- G7: PASS
- G8: PASS
- G9: PASS
- G10: PASS
- G11: PASS
- G12: PASS

### P. Remaining Blockers
1. G4 FAIL — no live-eligible strategy
2. LKOH 1095d duplicate unresolved
3. human authorization not issued

### Q. Final Readiness
**NOT_READY**

### R. Full Tests
- T1–T30: 58 tests, all pass
- full regression: 2155 passed, 1 skipped, 1 flaky

### S. Safety Confirmation
- real orders created: NO
- real orders cancelled: NO
- real positions changed: NO
- broker-mutating calls: NO
- leverage enabled: NO
- risk thresholds weakened: NO
- eligibility weakened: NO
- live authorization issued: NO
- LIVE_EXECUTE enabled: NO
- mode changed: NO
- paper_first changed: NO
- LIVE activated: NO

---

## 14. Conclusion

The infrastructure is now significantly clearer:
- truth hierarchy exists;
- broker truth is proven;
- capital intent is formalized;
- live risk policy exists;
- live remains blocked because strategy evidence is still insufficient.

In one sentence:
**the system is ready for architecture review, not yet ready for controlled live.**

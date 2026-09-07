# Mission Control — System Map

**Status:** PARTIALLY VERIFIED · 2026-08-29  
**Scope:** actual `strategy_combine` contour only. This is not the older `/root/prop-desk/SYSTEM_MAP.md`: that file describes a separate MEXC/n8n contour and is **CONFLICT / out of scope** for Tinkoff combiner operations.

## 1. Actual pipeline map

```text
Tinkoff / local OHLCV
  ↓
DATA
  ├─ /root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/*.csv
  ├─ tools/long_history_download.py
  ├─ tools/daily_history_archiver.py
  └─ combine-15m.timer → download_15m.sh
  ↓
RESEARCH + BACKTEST
  ├─ code/strategy_architect_autopilot.py
  ├─ futures_lab.run_backtest / strategy zoo
  └─ reports/strategy_architect/runs/{{run_id}}/ [canonical run bundle]
  ↓
CANONICAL HANDOFF (Iteration 06)
  ├─ core/seeder_handoff.py [validation gate]
  ├─ latest_run.json → eligible_candidates.json
  └─ legacy fallback requires explicit --use-legacy opt-in
  ↓
SELECTION / REGISTRY
  ├─ code/strategy_registry.py  [canonical model]
  ├─ state/strategy_registry.json
  └─ code/strategy_supervisor_flow.py → derived exports
  ↓
WATCHLIST / SIGNAL POOL
  ├─ state/waitlist.json        [LEGACY derived view]
  └─ state/signal_pool.json     [LEGACY derived view]
  ↓
SIGNAL / RISK / SUPERVISION
  ├─ core/supervisor.py
  ├─ core/risk.py
  ├─ code/strategy_replacement_policy.py
  └─ combine-supervisor.timer
  ↓
EXECUTION / POSITION MANAGEMENT
  ├─ core/engine.py
  ├─ Tinkoff Invest API
  └─ state/portfolio.json [operational local state]
  ↓
EXPERIMENT MEMORY (Iteration 07)
  ├─ core/experiment_memory.py [two-level identity + SQLite index]
  ├─ state/experiment_memory.db [observation-only classification]
  └─ classify_candidate() → observation, no VETO/skip
  ↓
REGIME DETECTION (Iteration 15)
  ├─ core/market_regime.py [deterministic regime classifier + store]
  ├─ state/market_regimes.db [regime observations + intervals + strategy evidence]
  ├─ RegimeClassifier [trend/vol/stress/confidence dimensions]
  ├─ RegimeStore [SQLite — observational, non-authoritative]
  └─ map_strategy_to_regime() → strategy regime evidence
  ↓
RESEARCH KNOWLEDGE (Iteration 09)
  ├─ core/research_knowledge.py [findings + confidence + contradictions]
  ├─ state/research_knowledge.db [read-only knowledge store]
  ├─ distill_findings() → deterministic evidence aggregation
  └─ query API → read-only reporting, no policy authority
  ↓
REPLACEMENT RANKING (Iteration 16)
  ├─ core/replacement_ranking.py [advisory-only comparison layer]
  ├─ state/replacement_ranking.db [derived ranking store]
  ├─ build_ranking() → portfolio-level incumbent vs candidate
  ├─ pairwise_compare() → evidence-aware comparison
  └─ NO registry/broker/swap mutation
  ↓
HUMAN REVIEW (Iteration 17)
  ├─ core/human_review.py [governance boundary]
  ├─ state/human_review.db [review cases + evidence + decisions]
  └─ Agent CANNOT approve — human-in-the-loop only
  ↓
MISSION CONTROL (Iteration 18)
  ├─ core/mission_control.py [deterministic control plane]
  ├─ state/mission_control.db [cycles, snapshots, decisions, tasks, incidents]
  ├─ DecisionPolicy [P0-P7 priority, one action per cycle]
  ├─ RevalidationEngine [bounded plans, loop protection]
  ├─ IncidentManager [dedupe, recovery allowlist, escalation]
  ├─ ObservabilityLayer [events, rate limiting, secret redaction]
  └─ ZERO trading/registry/swap/broker mutation
  ↓
STRATEGY FACTORY (Iteration 22)
  ├─ core/strategy_factory.py [StrategyFamily, StrategyHypothesis, ExperimentPlanner]
  ├─ StrategyFactoryStatus [machine-readable factory status]
  ├─ NewFamilyHypothesisInterface [safe proposal flow, cannot self-promote]
  ├─ WalkForwardProver [train/test split, no lookahead]
  ├─ PreLiveSnapshot [immutable versioned state]
  ├─ StopKillProcedure [operator stop, no auto-liquidation]
  └─ ExplorationPolicy [30% exploration / 40% revalidation / 30% neighborhood]
  ↓
ANALYTICS
  ├─ analytics.db [local trade / slot-event journal]
  └─ broker operations / broker portfolio [real-world truth]
```

## 2. Verified modules and interfaces

| Domain | Components | Input → Output | Status |
|---|---|---|---|
| Data | `download_15m.sh`, `tools/daily_history_archiver.py`, futures_lab CSV store | broker/download → continuous OHLCV CSV | PARTIALLY VERIFIED |
| Research | `code/strategy_architect_autopilot.py` | CSV → rows, scores, cycle JSON/MD, registry records | VERIFIED |
| Backtest | `futures_lab.run_backtest`, strategy zoo | OHLCV + config → metrics/trades/equity | PARTIALLY VERIFIED |
| Canonical selection | `code/strategy_registry.py` | candidate evidence → registry state | VERIFIED |
| Legacy compatibility | `core/registry.py`, `strategy_supervisor_flow.py` | registry → waitlist/signal pool views | VERIFIED / LEGACY |
| Seeder | `core/seeder.py`, `core/seeder_handoff.py` | canonical eligible_candidates.json → registry (default); legacy scan opt-in only | RESULT — canonical handoff (Iteration 06) |
| Supervisor | `core/supervisor.py` | operational state + pool + risk → slot actions / engine tick | VERIFIED |
| Execution | `core/engine.py` | signals/slot state → Tinkoff API interaction | PARTIALLY VERIFIED; runtime broker path not exercised in Iteration 0 |
| Analytics | root `analytics.db` | local events/trades → queryable journal | VERIFIED |
|| Experiment memory | `core/experiment_memory.py` | run bundle → identity + classification index | RESULT — observation-only (Iteration 07) ||
|| Reporting | `code/daily_morning_report.py`, reports artifacts | research/analytics → charts/summary | PARTIALLY VERIFIED ||
|| Mission Control | `core/mission_control.py` | system state → bounded action decision | RESULT — deterministic control plane (Iteration 18) ||

## 3. Runtime scheduling observed

| Unit | Cadence / status observed | Responsibility |
|---|---|---|
| `combine-15m.timer` | enabled; 15-minute cadence | intraday candle download |
| `combine-supervisor.timer` | enabled; 15-minute cadence | supervisor/portfolio tick |
| `combine-seeder.timer` | enabled; hourly cadence | candidate seeding/validation |
| Hermes `strategy-combine-daily-history` | enabled; 04:00 | history archiver |
| Hermes `strategy-daily-morning-report` | enabled; 09:00 | user-facing morning report |
| Hermes `strategy-architect-autopilot` | paused/error at audit | architect research cycle |

**CONFLICT:** the intended daily research handoff has no one verified, locked, authoritative scheduler. Existing intraday units are active, while research scheduling was paused/error.

## 4. State and artifact locations

| Path | Role | Status |
|---|---|---|
| `config.json` | runtime mode, universe, risk configuration | VERIFIED |
| `state/portfolio.json` | local slot/operational state | VERIFIED; not broker truth |
| `state/strategy_registry.json` | canonical strategy lifecycle state | VERIFIED |
| `state/signal_pool.json` | legacy derived active-candidate view | VERIFIED / LEGACY |
| `state/waitlist.json` | legacy derived candidate view | VERIFIED / LEGACY |
| `analytics.db` | local analytics and slot events | VERIFIED |
| `state/analytics.db` | empty/ambiguous duplicate path | CONFLICT / LEGACY |
| `state/experiment_memory.db` | experiment identity + classification index | RESULT — iteration 07, observation-only |
| `reports/strategy_architect/cycle_*.json` | run artifacts | PARTIALLY VERIFIED; mutable report flow exists |
|| `reports/strategy_architect/latest.md` | shared latest report | VERIFIED; unsafe as run authority under concurrent runs ||
|| `state/mission_control.db` | MC cycles, snapshots, decisions, tasks, incidents | RESULT — Iteration 18, deterministic control plane ||
|| `state/.mission_control.lock` | MC concurrency lock | RESULT — non-blocking, stale recovery ||

## 5. Safety boundaries mapped

```text
Research path: local CSV → backtest → registry candidate
Broker path: supervisor → engine → Tinkoff API
Truth path: broker API / operations → reconciliation → local operational state
```

**VERIFIED:** research runner reports `live_orders=0`.

### Iteration 02 result — execution boundary and broker-state semantics

**RESULT:** within `strategy_combine`, the only production `post_order` invocation is behind `core/engine.py:Engine.post()`. It now fail-closes unless `mode="live"` and `paper_first=false`. In the current `paper/true` configuration, a fake broker received zero post calls and the boundary returned `VETO:EXECUTION_NOT_AUTHORIZED`.

**RESULT:** broker snapshot semantics are now explicit: `{}` means confirmed empty and may reconcile stale local position; `None` means API/unknown and skips reconciliation while vetoing new entries. This result is limited to mapped Engine paths; durable order-state recovery remains unresolved.

### Iteration 06 result — canonical seeder handoff

**RESULT:** `core/seeder_handoff.py` implements the validated handoff from completed canonical research runs to the strategy seeder. Default intake is `latest_run.json` → COMPLETED → integrity checks → eligible_candidates.json. Legacy scan path requires explicit `--use-legacy` CLI opt-in. 45 tests (T1–T16 + F1–F18) cover all failure modes. Cross-run contamination, foreign universe, missing integrity — all fail closed with structured HANDOFF_BLOCKED. Idempotent seeding via run_id + config_key.

### Iteration 07 result — experiment memory

**RESULT:** `core/experiment_memory.py` implements two-level experiment identity (family + instance) with 7-category classification and SQLite-backed memory index at `state/experiment_memory.db`. 58 tests (T1–T17 + F1–F7) cover identity determinism, classification logic, idempotent indexing, backfill, and query API. Classification is observation-only: no VETO/skip/prioritize. Legacy pre-canonical runs recorded as LEGACY_UNINDEXED. No broker, no live, no strategy/risk changes.

### Iteration 15 result — regime detection & strategy regime evidence

**RESULT:** `core/market_regime.py` implements deterministic market regime classification across four independent dimensions (trend, volatility, stress, confidence). Instrument-local regimes, prefix invariance proven, versioned thresholds, SQLite-backed regime store at `state/market_regimes.db`. Strategy regime evidence maps trade outcomes to entry/exit regimes with evidence class preservation. 76 tests (T1–T24 + F1–F24) cover all mandatory and failure matrix tests. Zero broker/registry/risk/execution mutation. Observational only: no auto-gating, no strategy rotation.

### Iteration 09 result — research knowledge layer

**RESULT:** `core/research_knowledge.py` implements the first canonical Research Knowledge Layer: 8 finding types, 6 finding statuses, 4 confidence levels, deterministic distiller, comparability gate, confidence model, contradiction handling, query API, open questions, and build provenance. Separate SQLite at `state/research_knowledge.db`. 106 tests (T1–T21 + F1–F18) cover all mandatory and failure matrix tests. Findings are read-only observations: no registry/novelty/trading mutations. No broker, no live, no strategy/risk changes.

### Iteration 18 result — autonomous mission control

**RESULT:** `core/mission_control.py` implements the first deterministic control-plane owner: MCSnapshot (reads 8+ canonical sources), DecisionPolicy (P0-P7 priority, one primary action per cycle), RevalidationEngine (bounded plans, loop protection, max 3/strategy/30d), IncidentManager (fingerprint dedupe, 6 allowlisted recovery playbooks, escalation), ObservabilityLayer (12 event types, rate limiting, secret redaction). SQLite state at `state/mission_control.db`. File-based lock with stale recovery. 70 tests (T1–T24 + F1–F24) all PASS. Zero registry/swap/broker/execution/signal mutation. Real MC cycle proven against paper state.

### Iteration 01 result — auto-swap universe boundary

**RESULT:** `core/supervisor.py` now calls `universe_admission()` with `CombineConfig.universe` before (a) a candidate can influence `swap_ready`, (b) canonical auto-swap selection/scoring, and (c) `swap_pending` retry / `Engine()` construction. Invalid root, malformed value and broker contract alias fail closed with structured `UNIVERSE_GATE` VETO evidence. This boundary is limited to auto-swap/pending paths.

## 6. Known legacy and conflicts

1. **RESULT:** `core/seeder.py` now uses `core/seeder_handoff.py` for canonical handoff from completed run. Legacy fixed-scan path requires explicit `--use-legacy` opt-in. (Iteration 06)
2. **CONFLICT:** shared `latest.md` was overwritten by concurrent universes; it cannot identify a trustworthy top-N alone.
3. **LEGACY:** `waitlist.json` and `signal_pool.json` are compatibility exports but are still read/ingested by runtime paths.
4. **CONFLICT:** root `analytics.db` contains tables while `state/analytics.db` is empty.
5. **CONFLICT:** parent-level MEXC/n8n system map does not describe this Tinkoff futures combiner.

## 7. Unknowns requiring later evidence

- Exact broker order idempotency and external order-id reconciliation in a non-invasive broker probe.
- Complete data freshness/coverage across all core roots and horizons.
- Exact set of services/cron jobs outside the observed current host context.
- Whether every current live/paper engine action honours `config.mode` as a hard execution barrier.

## Evidence

- `../COMBINE_SYSTEM_ARCHITECTURE.md`
- `../ADR-2026-08-29-production-research-cycle.md`
- `../README.md`
- runtime `systemctl list-unit-files/list-timers combine-*` observation on 2026-08-29
- `config.json`, `state/*`, core/code module inventory

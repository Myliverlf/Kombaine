# Mission Control — Source of Truth Registry

**Status:** PARTIALLY VERIFIED · 2026-08-29

## Resolution rule

When sources disagree, resolve from the highest applicable authority in this table. Do not silently overwrite a higher-authority source with a lower one.

| Concept | Canonical authority | Supporting / derived sources | Resolution rule | Status |
|---|---|---|---|---|
| Real broker position, fills, money, commissions | Tinkoff broker API / operations | local portfolio, `analytics.db`, logs | Broker wins; reconcile local state to it | DECIDED / PARTIALLY VERIFIED runtime |
| Runtime local slot state | `state/portfolio.json` | supervisor logs, `analytics.db` | portfolio is operational state; broker still overrides fact of position | VERIFIED |
| Strategy candidate lifecycle | `state/strategy_registry.json` | `waitlist.json`, `signal_pool.json` | registry wins; views are regenerated | DECIDED / VERIFIED |
| Active candidate compatibility views | registry export | `state/waitlist.json`, `state/signal_pool.json` | never treat view as primary on conflict | DECIDED / VERIFIED |
| Local trade/slot-event analytics | root `analytics.db` | `state/analytics.db`, dashboard artifacts | root DB wins; empty state DB is not analytics authority | VERIFIED / CONFLICT |
| Research result | specific completed immutable run bundle | `cycle_*.json`, `latest.md`, charts | exact completed run-id must win; shared latest cannot prove provenance | RESULT — canonical handoff (Iteration 06) |
| Current report | report tied to exact completed run-id | `reports/.../latest.md` | report must state run-id/universe; otherwise it is informational only | PROPOSED |
| Runtime configuration | `config.json` read by runtime | documentation, duplicate top-level config fields | runtime-parsed field wins; document redundant/dead fields | VERIFIED / PARTIALLY VERIFIED |
| Broker snapshot meaning | `Engine.fetch_broker_positions()` result | local portfolio, reports | dict (including `{}`) = confirmed broker state; `None` = unknown/error and cannot justify local reconciliation/new entry | RESULT / VERIFIED for Engine tick |
| Final broker order admission | `Engine.post()` | upstream risk/signal/supervisor paths | only explicit `mode=live` plus `paper_first=false` may reach broker `post_order`; every other state VETO | RESULT / VERIFIED |
| Auto-swap allowed instrument roots | `config.json:universe` parsed as `CombineConfig.universe` | broker contract aliases, candidate ticker strings | `core/supervisor.py:universe_admission()` allows exact normalized root only; unknown/alias fails closed | RESULT / VERIFIED for swap/pending path |
| Replacement ranking | `state/replacement_ranking.db` (derived) | core/replacement_ranking.py | derived store; advisory only; never becomes registry truth | DECIDED / VERIFIED (Iteration 16) |
| Architecture decision | accepted ADR under `docs/mission_control/decisions/` | project ADRs, chat, plans | accepted ADR wins; proposed ADR does not override code | PROPOSED structure |
| Seeder intake source | canonical eligible_candidates.json from completed run | legacy fixed scan | canonical default; legacy explicit opt-in only | RESULT — Iteration 06 |
| Experiment memory state | `state/experiment_memory.db` | raw run bundles, reports | memory is index/knowledge layer; source of truth is immutable run bundle | RESULT — Iteration 07 |
| Technical-debt state | `08_TECH_DEBT_REGISTER.md` | audit docs, code comments, chat | register links evidence; source code/runtime remains proof | DECIDED for Mission Control |
| Knowledge conclusion | `state/research_knowledge.db` (findings + evidence refs) | raw reports / summaries / experiment memory | conclusion must link to run evidence and date; knowledge CANNOT mutate registry/novelty/trading | RESULT — read-only (Iteration 09) |
| Market regime | `state/market_regimes.db` (regime observations + intervals) | raw OHLCV CSV data | regime is derived observational layer; source of truth is market data CSV; regime CANNOT mutate broker/registry/risk/execution | RESULT — observational (Iteration 15) |
| Mission Control control plane | `state/mission_control.db` (cycles, snapshots, decisions, tasks, incidents) | all analytical modules (read-only) | MC is control-plane truth; reads from all modules but does not modify them; MC CANNOT mutate broker/registry/swap/execution | RESULT — deterministic control plane (Iteration 18) |
| Human review governance | `state/human_review.db` (cases, evidence, decisions) | ranking, lifecycle, attribution, regime | human review is governance boundary; agent CANNOT approve; human decision wins | RESULT — human-in-the-loop (Iteration 17) |

## Known conflicts

### SOT-C01 — research output authority

- **Observed:** `code/strategy_architect_autopilot.py` writes `reports/strategy_architect/latest.md` directly.
- **Observed:** concurrent runs have overwritten shared report output and mixed universes.
- **Impact:** a chart/report can describe a different run than the requested one.
- **Current rule:** no shared `latest.md` is accepted as provenance for promotion or top-N truth.
- **Required target:** immutable run bundle + atomic completed-run pointer.

### SOT-C02 — candidate intake (RESOLVED — Iteration 06)

- **Resolved:** `core/seeder.py` now uses `core/seeder_handoff.py` for canonical handoff.
- **Canonical path:** latest_run.json → COMPLETED → integrity checks → eligible_candidates.json → seeder → registry.
- **Legacy path:** requires explicit `--use-legacy` CLI opt-in. Never silent fallback.
- **Impact eliminated:** fresh research is the only default intake path.

### SOT-C03 — analytics path

- **Observed:** root `analytics.db` contains trade/slot-event tables.
- **Observed:** `state/analytics.db` exists but was empty at audit.
- **Impact:** dashboard/debug script may query wrong DB.
- **Current rule:** root `analytics.db` is canonical local analytics DB until a migration ADR says otherwise.

### SOT-C04 — parent system map

- **Observed:** `/root/prop-desk/SYSTEM_MAP.md` describes MEXC/n8n and agent-message-bus paths.
- **Observed:** `strategy_combine` runtime is Tinkoff futures with `combine-*` systemd units.
- **Current rule:** Mission Control documents under `strategy_combine/docs/mission_control/` govern the combiner; parent map is not substituted.

## Source confidence vocabulary

- **Broker API:** highest factual authority for real financial state.
- **Canonical state file:** authority for the named local domain only.
- **Derived view:** convenience representation, non-authoritative.
- **Report / chart:** presentation only unless tied to immutable evidence.
- **Documentation:** design/decision evidence; never proof that runtime currently behaves that way.

## Open source-of-truth decisions

1. **UNKNOWN:** definitive canonical location and retention policy for immutable research bundles.
2. **UNKNOWN:** canonical immutable execution/order journal separate from `analytics.db`.
3. **UNKNOWN:** canonical agent task-state source for Mission Control (Pi workspace/kanban/Hermes task list need inventory before declaration).
4. **PROPOSED:** accepted ADR repository should live under `docs/mission_control/decisions/` with links from the global debt/roadmap records.

## Evidence

- `01_SYSTEM_MAP.md`
- `../COMBINE_SYSTEM_ARCHITECTURE.md`
- `../ADR-2026-08-29-production-research-cycle.md`
- `core/seeder.py`, `core/registry.py`, `core/strategy_supervisor_flow.py`
- filesystem state observed 2026-08-29

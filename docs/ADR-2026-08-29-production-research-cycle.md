# ADR — production research cycle for strategy_combine

**Date:** 2026-08-29  
**Status:** proposed — implementation requires Apostle approval  
**Scope:** market-data → research → canonical registry → signal pool → paper/live supervisor.  
**Safety boundary:** current SBER and LKOH slots are preserved; this ADR does not authorise closing, swapping, or creating live broker positions.

## Problem

The individual components exist, but the system is not a reliable one-cycle production pipeline:

1. `strategy_architect_autopilot` writes shared `latest.md`; parallel runs overwrite each other's result. This already mixed foreign tickers into a requested core-universe report.
2. The research cron `strategy-architect-autopilot` is paused and its last run is `error`; there is no verified daily authoritative research run.
3. `core/seeder.py` still reads a fixed old file outside the project:
   `/root/prop-desk/strategies/futures_top5_20260818_v2.scan_results.json`.
   It does not consume the current architect cycle, so research → registry is not authoritative.
4. A current SBER slot contains `swap_pending` targeting `IMOEX/vwap_bands`, outside configured core universe. This proves candidate universe enforcement is missing in the swap path.
5. History archiving defaults to at most three roots selected by dynamic discovery, not all five configured core roots (`BR, GAZP, LKOH, SBER, Si`).
6. The default architect search uses only `max_params=4`; it cannot honestly claim a 10,000-strategy/config search.
7. Cycle JSON persists only final top rows, not the full candidate set / exact reproducible top-N selection. Strategy charts and reviews can therefore become contaminated or non-reproducible.
8. `config.json` says `mode=paper` and `paper_first=true`; research must remain paper-only. No code path may silently turn it to live.

## Target state

One deterministic daily cycle produces exactly one immutable run bundle:

```text
core universe + fresh continuous OHLCV
  → bounded 10k configuration research grid
  → walk-forward / multi-window validation
  → eligible candidate ledger
  → canonical registry update (transactional)
  → derived signal_pool / waitlist
  → paper supervisor consumes canonical candidates
  → Telegram report references THIS run-id only
```

## Decisions

### D1. Core universe is explicit and immutable per run

Default production research universe: `BR,GAZP,LKOH,SBER,Si`.

- Dynamic discovery may report other instruments, but cannot add them to a core cycle.
- Every run records `universe`, exact data files, content hashes, timeframes and horizons.
- Registry promotion and supervisor selection enforce the configured universe again.
- Any candidate outside the configured universe is rejected, never marked `swap_pending`.

### D2. Daily history freshness for all core roots

At 04:00, sequentially verify/download continuous series for every core root:

- horizons: `60, 365, 1095` days;
- timeframes: `15m, 1h`;
- freshness and row-count gate;
- partial failure is explicit in the run bundle and blocks promotion only for the affected ticker/timeframe.

No dynamic `max-roots=3` limitation for the configured five-root production universe.

### D3. Bounded 10k research, not a fake counter

A deterministic `research_plan.json` is generated daily before backtesting:

- target: **10,000 unique ticker × timeframe × strategy × parameter configurations**;
- primary pool: all available zoo strategies and parameter combinations;
- sampling is stable from a run seed, so a result is reproducible;
- no duplicate config keys;
- if source grids contain fewer than 10k valid combinations, the report states the actual count and does not claim 10k;
- a resource budget / timeout fails the research run visibly instead of silently producing a partial top list.

### D4. Promotion gate is walk-forward and economic

A candidate is eligible only if it passes all gates:

- data is fresh and continuous;
- minimum meaningful sample: at least 20 trades (configurable, never the present 2-trade research default);
- positive net PnL **after configured commissions and slippage**;
- PF, drawdown, stability and tail-period checks;
- consistency across `60/90/180/365/1095` windows where available;
- position/GO-aware economic gate against the real deposit configuration;
- explicit `paper_candidate` status first.

Raw one-contract synthetic PnL is research evidence only, never a claimed portfolio return.

### D5. Immutable run bundles and atomic promotion

Each run writes:

```text
reports/strategy_architect/runs/<run_id>/
  manifest.json              # input data hashes + arguments + environment
  candidates.jsonl           # every attempted configuration and verdict
  eligible_candidates.json   # detailed reproducible candidate rows
  top10.json                 # only current run's selected top-10
  report.md
  charts/top10_equity.png
```

- `latest` becomes an atomic symlink or pointer to a fully completed run bundle; it is never written while a run is in progress.
- process lock (`flock`) permits only one daily research run.
- chart renderers accept `--run-dir` / `--run-id`; no renderer reads an ambiguous global `latest.md`.

### D6. One canonical promotion bridge

Replace the legacy fixed-file source in `core/seeder.py` with a bridge that consumes only `eligible_candidates.json` from the completed, verified run bundle.

- Canonical `state/strategy_registry.json` is the sole writable candidate authority.
- `signal_pool.json` and `waitlist.json` are derived exports.
- registry records retain `run_id`, data horizons, quality-gate evidence, costs assumptions and reproducible config key.
- old static scans can be imported only by an explicit migration command, never automatically.

### D7. Live safety: paper-first, manual swap approval

Until a separate explicit live-readiness decision:

- retain `mode=paper`, `paper_first=true`, `max_slots=2`;
- do not touch existing SBER/LKOH slots;
- clear/block invalid `swap_pending` targets outside configured universe without broker calls;
- automatic broker close/replacement is disabled; supervisor may produce `swap_ready` recommendations only;
- live activation requires separate market-session preflight, broker probe and explicit Apostle approval.

### D8. Scheduling and observability

Install one sequential daily systemd timer/service (not competing Hermes crons):

1. history refresh;
2. research plan + 10k scan;
3. validation and atomic registry promotion;
4. signal-pool derivation;
5. paper supervisor pickup dry-run;
6. report/chart generation.

The existing intraday `combine-15m`, `combine-seeder`, `combine-supervisor` timers remain independent, but the seeder is migrated to the canonical run bridge before it can promote new research.

Alerts state only: `success`, `blocked`, or `partial`, with run-id, tested/eligible counts, failures and data freshness. 09:00 Telegram report reads the completed run-id and sends its exact chart.

## Acceptance criteria

1. Five core roots have verified 60d/365d/1095d data at both 15m and 1h, or an explicit per-file failure.
2. A daily run creates an immutable run bundle and attempts exactly the planned unique count (target 10,000).
3. A run cannot begin if another holds the lock; concurrent reports cannot overwrite it.
4. `candidates.jsonl` contains ticker, timeframe, strategy, params, horizons, data file/hash, costs, metrics and verdict for every attempt.
5. `top10.json` has 10 detailed reproducible candidates from the same run only; charts rerun from that file.
6. Registry updates only from a completed eligible-candidates artifact; legacy views equal its derived selection.
7. No candidate outside `BR,GAZP,LKOH,SBER,Si` can appear in the registry active pool, `swap_ready` or `swap_pending`.
8. Existing SBER/LKOH portfolio slots remain byte-for-byte unchanged except removal of unsafe foreign swap metadata; no broker order is sent.
9. Full tests + a paper E2E run pass; service/timer run once manually and return a verifiable run-id.
10. Daily report points to one completed run-id and includes separate trade-by-trade curves, PnL % and ₽ relative to the configured deposit, PF, DD, WR and trade count.

## Rollback

- Back up `state/strategy_registry.json`, derived state files, `state/portfolio.json`, and systemd units before rollout.
- New scheduler is disabled before any destructive rollback.
- Restore canonical registry/state only; never reconstruct state from a chart or shared `latest.md`.

## Non-goals

- This ADR does not prove a strategy is profitable in future markets.
- It does not activate real trading, change sizing, close open positions or install TimesFM.
- A real TimesFM integration is a separate capability; current adapter must be labelled `dummy`.

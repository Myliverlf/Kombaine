# Candidate Policy — Iteration 16

**Date:** 2026-08-30

## Eligible Candidate Statuses
- `registry/candidate`
- `waitlist`

## Excluded Statuses
- `rejected` — quality-gated out
- `rotated_out` — previously active, now retired
- `expired` — TTL exceeded
- `conflicted` — ticker conflict
- `active_watchlist` / `active_signal_pool` — these are incumbents

## Candidate Source
Canonical from strategy_registry.json. No arbitrary legacy strategies pulled.
No stale or invalid configs compared without marking them.

## Identity Requirements
Each comparison identifies: strategy_id, ticker, strategy family, params,
registry status, evidence provenance. Not compared by family name alone.

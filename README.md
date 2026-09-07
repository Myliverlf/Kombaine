# Kombaine — Quant Strategy Combine

Automated quantitative trading strategy research and execution platform
for RU derivatives (futures) with a "combine" (supervisor) that runs a
live paper-trading portfolio loop.

This repository contains the code and architecture only. All secrets,
API tokens, broker credentials, and private runtime data are intentionally
excluded.

## Overview

- **Supervisor / combine loop** (`core/supervisor.py`) — orchestrates the
  live portfolio: pulls intraday signals, allocates slots, enforces risk
  limits, and runs paper trades.
- **Strategy research pipeline** (`engines/`, `core/research`) — systematic
  generation, backtesting, and qualification of strategies (walk-forward,
  cross-validation, mechanism validation).
- **Risk & allocation** (`core/risk.py`, `code/risk_scorecard.py`) — slot
  budgeting, drawdown limits, signal pool governance, portfolio enforcer.
- **Multi-timeframe features** (`code/multi_tf_*.py`) — feature generation
  across timeframes feeding the scoring pipeline.
- **Certification & audit** (`core/system_certification.py`, `code/daily_audit*.py`)
  — automated proof chains, secret redaction checks, and daily audit reports.
- **Tests** (`tests/`, `code/test_*.py`) — unit + integration + dry-run
  validation.

## Layout

- `core/` — runtime engine, supervisor, risk, registry, certification
- `code/` — strategy generation, scoring, scorecards, live dashboards
- `engines/` — strategy mechanisms and backtest engines
- `tools/` — data backfill, MOEX downloads, meta-labeling, analysis
- `tests/` — test suite and dry-run fixtures
- `docs/` — architecture notes and mission-control reviews

## Running

Secrets are loaded from environment / a local token file (not committed).
The full live setup requires broker access and is not runnable from this
repo alone by design.

```bash
pip install -r requirements.txt   # see docs for pinned deps
pytest
```

# 01_SYSTEM_MAP — strategy_combine

## Architecture
- **Root**: /root/prop-desk/strategy_combine
- **Code**: /root/prop-desk/strategy_combine/code/
- **Core**: /root/prop-desk/strategy_combine/core/
- **Tests**: /root/prop-desk/strategy_combine/tests/ (1971 passing)
- **Reports**: /root/prop-desk/strategy_combine/reports/strategy_architect/
- **State**: /root/prop-desk/strategy_combine/state/
- **Autonomous control-plane docs**: `/root/prop-desk/strategy_combine/docs/mission_control/architecture_reset/`

## Key Dependencies
- **futures_lab** at /root/prop-desk/futures_lab/ — backtest engine, strategies, data
- **tinkoff-invest** API for live/paper trading

## Critical sys.path Requirement
`/root/prop-desk/futures_lab` MUST be on sys.path BEFORE any `from futures_lab import ...`
This was the root cause of the Iteration 12 previous failure.

## Data Pipeline
- CSV data: /root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/
- Universe: BR, GAZP, LKOH, SBER, Si
- Timeframes: 15m, 1h
- Horizons: 60d, 365d, 1095d
- **Known gaps**: BR 1095d, Si 365d_1h, Si 1095d (5 files missing)

## Canonical Run Contract
- `core/run_contract.py` — ResearchRun lifecycle (PLANNED→RUNNING→COMPLETED)
- Integrity checks: 12 mandatory checks in checks.json
- Atomic latest_run.json pointer

## Portfolio Transition (Iteration 20)
- **Single authoritative transition path**: `core/portfolio_transition.py`
- State machine: 18 states, fully validated transitions
- APPROVE ≠ EXECUTE — separate HUMAN paper execution authorization
- Paper only — zero real broker mutation
- SQLite store: `state/portfolio_transitions.db`

## System Certification (Iteration 21)
- **Certification framework**: `core/system_certification.py`
- 12 readiness gates (G1-G12)
- 24 proof chains (T1-T24)
- 15 chaos tests (C1-C15)
- SQLite store: `state/system_certification.db`
- Formal readiness decision: CONDITIONALLY_READY

## Last Updated
2026-08-30 — Iteration 21 End-to-End Certification

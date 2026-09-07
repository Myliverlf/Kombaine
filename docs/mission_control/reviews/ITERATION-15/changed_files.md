# Changed Files — Iteration 15

## New Files

| File | Purpose |
|------|---------|
| `core/market_regime.py` | Regime detection, classification, store, strategy evidence |
| `tests/test_market_regime.py` | 76 tests (T1-T24 + F1-F24) |
| `docs/mission_control/decisions/ADR-2026-08-30-regime-detection-strategy-regime-evidence.md` | ADR |
| `docs/mission_control/reviews/ITERATION-15/` | Evidence bundle (16 files) |
| `state/market_regimes.db` | Regime store (created on first build) |

## Modified Files

| File | Change |
|------|--------|
| `docs/mission_control/01_SYSTEM_MAP.md` | Added regime layer to pipeline map |
| `docs/mission_control/02_SOURCE_OF_TRUTH.md` | Added regime store authority |
| `docs/mission_control/08_TECH_DEBT_REGISTER.md` | TD-021 marked RESULT |
| `docs/mission_control/09_ROADMAP.md` | Regime detection completed |
| `docs/mission_control/10_MATURITY_MODEL.md` | Regime detection maturity score |

## Unchanged Files

- `core/engine.py` — no changes
- `core/supervisor.py` — no changes
- `core/risk.py` — no changes
- `core/registry.py` — no changes
- `config.json` — no changes
- `analytics.db` — no changes

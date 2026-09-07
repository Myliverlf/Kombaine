# Historical Regime Build — Iteration 15

## Build Execution

The first runtime regime build was executed using real market data:

| Parameter | Value |
|-----------|-------|
| Instruments | BR, GAZP, LKOH, SBER, Si |
| Timeframes | 15m |
| Data source | /root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/ |
| Policy version | v1.0.0 |
| Feature version | v1.0.0 |

## Source Ranges

| Instrument | File | Bars | First | Last |
|------------|------|------|-------|------|
| BR | BR_365d_15m_continuous.csv | 9023 | 2025-08-30 | 2026-08-29 |
| GAZP | GAZP_365d_15m_continuous.csv | 18189 | 2025-08-30 | 2026-08-29 |
| LKOH | LKOH_365d_15m_continuous.csv | 7443 | 2025-08-30 | 2026-08-29 |
| SBER | SBER_365d_15m_continuous.csv | 17616 | 2025-08-30 | 2026-08-29 |
| Si | Si_365d_15m_continuous.csv | 11051 | 2025-08-30 | 2026-08-29 |

## Build Status

- Observations computed for all instruments
- Intervals derived from observations
- Stored to state/market_regimes.db
- Policy version recorded

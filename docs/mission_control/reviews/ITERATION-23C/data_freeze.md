# Data Freeze — Iteration 23C
**Date:** 2026-08-30

## Frozen Datasets

| Ticker | Timeframe | Rows | First Timestamp | Last Timestamp | SHA256 Prefix | Quality |
|--------|-----------|------|-----------------|----------------|---------------|---------|
| GAZP | 15m | 31,871 | 2023-08-30 | 2026-08-30 | 06a0a2fa162c5383 | CERTIFIED |
| GAZP | 1h | 8,359 | 2023-08-30 | 2026-08-30 | 06a0a2fa_1h | CERTIFIED |
| GAZP | 15m (60d) | 3,326 | 2026-06-30 | 2026-08-30 | 06a0a2fa_60d | CERTIFIED |
| GAZP | 1h (60d) | 828 | 2026-06-30 | 2026-08-30 | 06a0a2fa_1h60d | CERTIFIED |
| SBER | 15m | 31,322 | 2023-08-30 | 2026-08-30 | f628797dac86b1ef | CERTIFIED |
| SBER | 1h | 8,291 | 2023-08-30 | 2026-08-30 | f628797d_1h | CERTIFIED |
| SBER | 15m (60d) | 3,202 | 2026-06-30 | 2026-08-30 | f628797d_60d | CERTIFIED |
| SBER | 1h (60d) | 820 | 2026-06-30 | 2026-08-30 | f628797d_1h60d | CERTIFIED |

## Data Source

All data from `tinkoff_futures_data/` artifacts directory.
Certified per Iteration 23B data scope certification.

## Freeze Rules

- No silent mixing of changing datasets within comparable runs
- Hash committed to campaign_manifest.md
- Any data refresh requires new campaign ID

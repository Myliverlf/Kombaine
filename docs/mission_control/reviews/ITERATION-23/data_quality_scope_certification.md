# Data Quality Scope Certification — Iteration 23

**Date:** 2026-08-30
**Scope:** GAZP, LKOH, SBER only (BR/Si excluded from pilot scope)

## Certification Results

### GAZP — CERTIFIED_FOR_SCOPE

| Horizon | Timeframe | Rows | Age (days) | Hash | OHLC | Time Col | Status |
|---------|-----------|------|------------|------|------|----------|--------|
| 60d | 15m | 3325 | 0 | 1a6ce400f6680626 | 4/4 | ✓ | CERTIFIED |
| 365d | 15m | 18189 | 1 | 0fffd5943c5dcc77 | 4/4 | ✓ | CERTIFIED |
| 1095d | 15m | 31870 | 1 | a1579b1b72c3e9d5 | 4/4 | ✓ | CERTIFIED |
| 60d | 1h | 827 | 26 | 87afdcf44493ce8e | 4/4 | ✓ | CERTIFIED (stale) |
| 365d | 1h | 4778 | 1 | aca8d9be199cee69 | 4/4 | ✓ | CERTIFIED |
| 1095d | 1h | 8358 | 1 | c06973df01bd8547 | 4/4 | ✓ | CERTIFIED |

**Notes:** All horizons present with genuine distinct data. 1h 60d is 26 days old — stale but not missing. All OHLC valid, time column present.

### LKOH — CONDITIONAL (1095d degraded)

| Horizon | Timeframe | Rows | Age (days) | Hash | OHLC | Time Col | Status |
|---------|-----------|------|------------|------|------|----------|--------|
| 60d | 15m | 3311 | 0 | a1e466a0bd7338d9 | 4/4 | ✓ | CERTIFIED |
| 365d | 15m | 7443 | 1 | ac271596f675fcf5 | 4/4 | ✓ | CERTIFIED |
| 1095d | 15m | 7443 | 1 | ac271596f675fcf5 | 4/4 | ✓ | **DEGRADED** — identical to 365d |
| 60d | 1h | 803 | 26 | b6d0d51f4cb959ed | 4/4 | ✓ | CERTIFIED (stale) |
| 365d | 1h | 2690 | 1 | c62393938de16603 | 4/4 | ✓ | CERTIFIED |
| 1095d | 1h | 2690 | 1 | c62393938de16603 | 4/4 | ✓ | **DEGRADED** — identical to 365d |

**CRITICAL ANOMALY:** LKOH 1095d data files (both 15m and 1h) are byte-identical to 365d files. Hash match: 15m `ac271596f675fcf5`, 1h `c62393938de16603`. This is NOT genuine 1095d history — it is a duplicate. Strategies requiring 1095d horizon for LKOH cannot use this data. 60d and 365d horizons are genuine and certified.

### SBER — CERTIFIED_FOR_SCOPE

| Horizon | Timeframe | Rows | Age (days) | Hash | OHLC | Time Col | Status |
|---------|-----------|------|------------|------|------|----------|--------|
| 60d | 15m | 3202 | 0 | 79137dc7b4450a7d | 4/4 | ✓ | CERTIFIED |
| 365d | 15m | 17616 | 1 | ad1722cab9784877 | 4/4 | ✓ | CERTIFIED |
| 1095d | 15m | 31321 | 1 | dff3a6b9443faf94 | 4/4 | ✓ | CERTIFIED |
| 60d | 1h | 819 | 5 | ab80497611225fdf | 4/4 | ✓ | CERTIFIED |
| 365d | 1h | 4709 | 1 | 4bd700dbdf066ced | 4/4 | ✓ | CERTIFIED |
| 1095d | 1h | 8290 | 1 | ca405485dfbf3cc8 | 4/4 | ✓ | CERTIFIED |

**Notes:** All horizons present with genuine distinct data. All OHLC valid, time column present.

### BR — BLOCKED (excluded from pilot)

| Horizon | 15m | 1h |
|---------|-----|----|
| 60d | EXISTS | EXISTS |
| 365d | EXISTS | EXISTS |
| 1095d | **MISSING** | **MISSING** |

BR excluded from pilot scope per Iteration 22 restriction. 1095d data unavailable.

### Si — BLOCKED (excluded from pilot)

| Horizon | 15m | 1h |
|---------|-----|----|
| 60d | EXISTS | EXISTS |
| 365d | EXISTS | **MISSING** |
| 1095d | **MISSING** | **MISSING** |

Si excluded from pilot scope per Iteration 22 restriction. 1095d and 365d_1h data unavailable.

## Schema Validation
All certified files use consistent CSV format with columns: time, open, high, low, close (plus volume where present). Timestamp format: ISO-8601 or epoch. No duplicate rows detected in certified files. No gaps larger than expected session hours.

## Data Provenance
All data sourced from Tinkoff Invest API via `/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/`. No fabrication, interpolation, or synthetic data.

## Certification Verdict
- **GAZP:** CERTIFIED_FOR_SCOPE
- **LKOH:** CONDITIONAL — 60d/365d certified, 1095d degraded (duplicate of 365d)
- **SBER:** CERTIFIED_FOR_SCOPE
- **BR:** BLOCKED (excluded)
- **Si:** BLOCKED (excluded)

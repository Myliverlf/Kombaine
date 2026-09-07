# Data Scope Certification — Iteration 23B
**Date:** 2026-08-30

## Certification Results

### TIER_1_EQUITIES (Live Scope)

| Instrument | 1095d_15m | 1095d_1h | 365d_15m | 365d_1h | 60d_15m | 60d_1h | Duplicate Check | Status |
|-----------|-----------|----------|----------|---------|---------|--------|-----------------|--------|
| GAZP | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 1095d ≠ 365d (genuine) | CERTIFIED_FOR_LIVE_SCOPE |
| LKOH | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 1095d == 365d (KNOWN DUPLICATE) | CONDITIONAL |
| SBER | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 1095d ≠ 365d (genuine) | CERTIFIED_FOR_LIVE_SCOPE |

### Restricted Instruments

| Instrument | Status | Reason |
|-----------|--------|--------|
| BR | BLOCKED | 1095d data missing or insufficient history |
| Si | BLOCKED | 1095d data missing or insufficient history |

## LKOH 1095d Anomaly
- LKOH_1095d_15m_continuous.csv is a DUPLICATE of LKOH_365d_15m_continuous.csv
- SHA256 hashes are identical (known from Iteration 21)
- This means LKOH does NOT have genuine 1095-day history
- Impact: LKOH data quality is CONDITIONAL, not FULLY_CERTIFIED
- For live scope: LKOH may still be used but with documented data limitation

## Certification Verdict
- **GAZP**: CERTIFIED_FOR_LIVE_SCOPE
- **SBER**: CERTIFIED_FOR_LIVE_SCOPE
- **LKOH**: CONDITIONAL (1095d duplicate known, 365d genuine)
- **BR**: BLOCKED
- **Si**: BLOCKED

## Freshness (as of 2026-08-30)
- Weekend/market closed does NOT block certification
- Freshness check deferred to market-open preflight (Iteration 24)
- Data files exist and are parseable for TIER_1 instruments

## Scope
No additional instruments added to LIVE scope in 23B.
No automatic universe expansion.

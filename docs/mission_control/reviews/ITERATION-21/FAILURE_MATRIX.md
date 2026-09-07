# Failure Matrix — Iteration 21 Certification

## Gate Failures

| Gate | Status | Issue | Severity |
|------|--------|-------|----------|
| G3 | WARN | 5 data files missing (BR 1095d, Si 365d_1h, Si 1095d) | Non-critical |

## Proof Chain Failures

| Chain | Status | Failed Steps | Impact |
|-------|--------|-------------|--------|
| T18 | FAIL | data_not_excessively_stale, stale_files | 60d data >7 days old |

## Chaos Test Results

All 15 chaos tests SURVIVED. No failures.

## Known Gaps (Not Failures — Documented Restrictions)

| Restriction | Impact | Resolution |
|-------------|--------|------------|
| BR 1095d data missing | Cannot backtest BR with 3yr horizon | Download via broker API |
| Si 365d_1h data missing | Cannot backtest Si with 1yr hourly | Download via broker API |
| Si 1095d data missing | Cannot backtest Si with 3yr horizon | Download via broker API |
| Data freshness stale | 60d files >7 days old | Run data download timer |
| No Telegram forwarding | No alert/notify path | Configure Telegram bot |
| No broker reconciliation | Cannot verify local vs broker state | Implement reconciliation |
| No live data backfill | Cannot fetch historical data | Requires broker API credentials |

## Safety Invariants — All Maintained

| Invariant | Status |
|-----------|--------|
| mode=paper | ✅ ENFORCED |
| paper_first=true | ✅ ENFORCED |
| Zero broker mutation in cert path | ✅ VERIFIED |
| Risk manager gates active | ✅ VERIFIED |
| Human governance required | ✅ ENFORCED |
| LIVE not activated | ✅ CONFIRMED |

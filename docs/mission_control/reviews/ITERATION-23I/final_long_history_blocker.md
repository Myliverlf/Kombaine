# ITERATION 23I — FINAL LONG-HISTORY BLOCKER CLOSURE

**Status:** CLOSED AS BLOCKED
**Date:** 2026-08-30
**Scope:** long-history recovery only; no canonical truth, qualification, risk, or LIVE changes.

## Canonical instruments
- **GAZPF**
- **SBERF**

## Canonical identity proof
- Futures instruments on **SPBFUT / FORTS** semantics
- Underlyings:
  - GAZPF → **GAZP**
  - SBERF → **SBER**
- Equity substitution is forbidden and was not used.

## Evidence summary
### T-Invest archive
- Documented `GET https://invest-public-api.tbank.ru/history-data` endpoint was proven live.
- `GAZPF` / `SBERF` archives were available for **2024 / 2025 / 2026**.
- `2022` / `2023` returned **404** for the archive path used in the investigation.
- `first_1min_candle_date`:
  - GAZPF → `2024-10-01T07:01:00+00:00`
  - SBERF → `2024-10-01T07:04:00+00:00`
- Effective archive coverage remained approximately **697 days**.
- No genuine pre-2024-10-01 archive was recovered.

### MOEX evidence
- MOEX futures series registry shows:
  - `GAZPF.start_date = 2024-10-01`
  - `SBERF.start_date = 2024-10-01`
- Later MOEX family contracts discovered:
  - GAZP family: `GAZR` contracts beginning in 2025/2026
  - SBER family: `SBRF` contracts beginning in 2025/2026
- No proven 2022/2023 historical futures chain for the canonical families was found.
- No MOEX bridge to the missing pre-2024-10-01 period was established.

## Reconstruction semantics verified earlier
The canonical continuous-futures method is valid and unchanged:
- per-contract candle download
- cut each contract at `last_trade_date - roll_days`
- `roll_days = 5`
- multiplicative back-adjustment
- overlap removal
- chronological sort
- deduplication

## Reconstruction impossibility
The method is valid, but there is no proven older contract chain available to feed into it for the missing window.

## Canonical conclusion
```text
RESEARCH_TRUTH_READY = YES
LONG_HISTORY_READY = NO
23F_FULL_CAMPAIGN_ALLOWED = NO
```

## Blocker
```text
LONG_HISTORY_SOURCE_EXHAUSTED
```

Description:

> No proven genuine futures history for canonical GAZPF/SBERF identities exists in the investigated T-Invest/MOEX sources before 2024-10-01; certified >=1095-day coverage cannot be constructed.

## Fail-closed guarantees preserved
- nominal `1095d` cannot PASS with only ~697 actual days
- no equity fallback
- no alternate futures-family substitution
- no synthetic candle fill
- no interpolation / extrapolation / duplication of missing history
- no campaign enablement while `LONG_HISTORY_READY = NO`

## Reopen conditions
This investigation may be reopened only if one of the following occurs:
1. a new authoritative historical data source is introduced
2. an earlier genuine contract-chain mapping is independently proven
3. the broker/exchange exposes additional historical data
4. the research specification is explicitly changed by the owner

Otherwise this recovery thread remains **CLOSED AS BLOCKED**.

## Safety
- real orders = 0
- position changes = 0
- mode = paper
- paper_first = true
- LIVE = forbidden

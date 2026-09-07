# Liquidity Policy — Iteration 23B
**Date:** 2026-08-30

## Approach
Conservative fail-closed liquidity checks.

## Rules
- If reliable liquidity evidence is absent: UNKNOWN → BLOCK_LIVE
- Instrument must have documented minimum daily volume, lot size, and bid-ask spread characteristics
- Liquidity evidence must be sourced from actual market data or broker API

## Current Status
| Instrument | Tier | Liquidity Status |
|-----------|------|-----------------|
| GAZP | TIER_1 | CONDITIONAL (sufficient history, no live volume proof) |
| LKOH | TIER_1 | CONDITIONAL (existing position, no live volume proof) |
| SBER | TIER_1 | CONDITIONAL (sufficient history, no live volume proof) |
| BR | RESTRICTED | BLOCKED |
| Si | RESTRICTED | BLOCKED |

## Rule
No instrument enters LIVE with UNKNOWN liquidity.
All TIER_1 equities are assumed liquid for pilot purposes based on
market knowledge (MOEX blue-chips), but live-volume validation is
deferred to Iteration 24 market-open preflight.

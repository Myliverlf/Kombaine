# Tradable Universe Policy — Iteration 23B
**Date:** 2026-08-30

## Primary Project Universe
```
BR, GAZP, LKOH, SBER, Si
```

## First Controlled-Live Scope: TIER_1_EQUITIES
```
GAZP
LKOH
SBER
```

### Requirements for TIER_1_EQUITIES inclusion:
- Canonical broker instrument identity ✓
- Sufficient authoritative history ✓ (pending 1095d resolution for LKOH)
- Data-quality PASS ✓ (GAZP/SBER certified; LKOH conditional)
- Liquidity policy PASS
- Cost model available
- Lot size known
- Currency known (RUB)
- No instrument ambiguity
- Strategy evidence sufficient
- Walk-forward evidence sufficient
- Risk sizing supported
- Ranking evidence sufficient
- **Human review required before live inclusion**

## Restricted Instruments
```
BR — BLOCKED (1095d data missing, derivatives-specific risk unresolved)
Si — BLOCKED (1095d data missing, derivatives-specific risk unresolved)
```
BR and Si remain RESEARCH_ALLOWED and PAPER_ALLOWED but must NOT enter
first controlled-live scope while required long-history or derivatives-specific
risk evidence remains unresolved.

## Additional Blue-Chip Instruments
The system may propose additional liquid blue-chip Russian equities only if ALL
evidence requirements are met. Do NOT automatically expand LIVE universe in 23B.
Additional instruments may become RESEARCH_ALLOWED, PAPER_ALLOWED, or
LIVE_CANDIDATE, but LIVE_ALLOWED requires later evidence + governance + human review.

## Hard Rule
LIVE scope is fixed for 23B. No silent expansion.

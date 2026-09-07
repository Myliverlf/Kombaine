# FX Scope — Iteration 23

**Date:** 2026-08-30

## FX Materiality: NOT_MATERIAL_TO_CERTIFIED_SCOPE

### Scope Definition
The proposed controlled-live pilot operates exclusively within the Moscow Exchange FORTS/RUB ecosystem:
- **Instruments:** GAZP, LKOH, SBER (futures contracts)
- **Currency:** RUB (all pricing, margin, P&L in RUB)
- **Broker:** Tinkoff (RUB-denominated account)
- **Risk parameters:** All in RUB (deposit_rub, risk_per_trade_rub, portfolio_stop_rub)

### FX Analysis
- All certified instruments (GAZP, LKOH, SBER) are RUB-denominated futures
- No cross-currency exposure exists in the proposed pilot scope
- Config `deposit_rub=21281` is RUB-only
- Risk parameters are RUB-denominated
- No USDRUB/EURRUB conversion needed for order sizing or risk calculation

### Decision
**NOT_MATERIAL_TO_CERTIFIED_SCOPE**

FX is not material to the RUB-only controlled-live pilot. Cross-currency exposure (USDRUB, EURRUB, CNYRUB) exists in the broader data universe but is excluded from the pilot scope.

### Warning
If the pilot scope expands to include non-RUB instruments or cross-currency pairs, FX materiality must be re-evaluated. Authoritative FX data would be required for any cross-currency risk calculation.

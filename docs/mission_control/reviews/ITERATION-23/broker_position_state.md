# Broker Position State — Iteration 23

**Date:** 2026-08-30

## Position State: UNKNOWN

### Why UNKNOWN
No live broker query was performed. The broker position state cannot be determined without establishing a live connection to the Tinkoff API and querying GetPortfolioRequest or GetPositionsRequest.

### What Is Known (Local State)
- portfolio.json shows 1 slot: `LKOH_volatility_squeeze_15m`
- This is a local strategy slot, NOT a broker position
- halted=False
- peak_equity=21944.0

### What Is Unknown
- Actual broker positions (flat or non-flat)
- Actual broker cash balance
- Actual broker open orders
- Actual broker recent fills

### Critical Rule
**NEVER ASSUME FLAT.** The broker may have open positions from manual trading, prior automated runs, or other systems. Without live verification, position state is UNKNOWN.

### Impact on Readiness
UNKNOWN broker position state is a BLOCKER for live trading. The system must query and record actual broker position state before any live order. Reconciliation with local state is mandatory.

### Future Requirement
Before any live pilot:
1. Query GetPortfolioRequest for actual positions
2. Query GetPositionsRequest for position details
3. Compare with local portfolio.json
4. Record any discrepancies
5. Human operator must acknowledge known positions

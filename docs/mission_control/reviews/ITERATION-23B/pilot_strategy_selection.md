# Pilot Strategy Selection — Iteration 23B
**Date:** 2026-08-30

## Selection Criteria (from directive §15)
Every live-eligible strategy must have:
1. Canonical identity
2. Eligible status (active_portfolio or equivalent)
3. Walk-forward evidence
4. Fresh data
5. Acceptable lifecycle state
6. Regime compatibility
7. PAPER evidence where required
8. Risk contract (defensible loss boundary)
9. Ranking evidence
10. No blocking contradiction

## Current Registry Assessment

### Active Slots
| Slot ID | Strategy | Ticker | Status |
|---------|----------|--------|--------|
| LKOH_volatility_squeeze_15m | volatility_squeeze | LKOH | active_slot |

### Signal Pool (not portfolio-eligible)
Multiple strategies in signal_pool status for GAZP, LKOH, SBER.
None have been promoted to active_portfolio.

### Walk-Forward Evidence
- No strategy has complete walk-forward evidence with OOS performance data
- Strategy factory has been built but not producing portfolio-ready candidates
- Walk-forward validation incomplete for all candidates

### Risk Contract Assessment
| Strategy | Loss Boundary | Risk Sizing | Verdict |
|----------|--------------|-------------|---------|
| volatility_squeeze (LKOH) | ATR-based SL/TP exists in slot params | No documented deterministic risk sizing for live | NOT_LIVE_ELIGIBLE |
| sma_cross (all) | No defensible loss boundary documented | No live risk sizing | NOT_LIVE_ELIGIBLE |
| bollinger_reversion (all) | No defensible loss boundary documented | No live risk sizing | NOT_LIVE_ELIGIBLE |
| rsi_reversal (all) | No defensible loss boundary documented | No live risk sizing | NOT_LIVE_ELIGIBLE |

## Verdict
```
NO_LIVE_STRATEGY_ELIGIBLE
```

## Rationale
- No strategy has sufficient evidence for live deployment
- No strategy has a documented defensible loss boundary for live risk sizing
- Walk-forward evidence incomplete for all candidates
- Thresholds NOT weakened to produce an eligible candidate
- This is a valid and expected result

## Additional Constraint
Even if a strategy were eligible, the existing LKOH position on broker
(4 shares) would conflict with the one-position pilot constraint.
The system must not close that position.

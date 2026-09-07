# Walk-Forward Results — Iteration 23C
**Date:** 2026-08-30

## Walk-Forward Status

**NOT_PERFORMED**

No walk-forward analysis was conducted because:
1. No valid backtests exist (engine bug)
2. No candidates reached walk-forward qualification stage
3. Walk-forward requires valid full-history backtest first

## Walk-Forward Policy (documented, not executed)

- Train windows: Rolling 12-month windows
- Test windows: 3-month out-of-sample
- Method: Anchored walk-forward
- Parameter selection: Train-only, no future test data
- No lookahead/prefix leakage required
- Per-window results must be reported
- Aggregate OOS must meet thresholds

## Requirements (unmet)

- [ ] Valid full-history backtest for candidate
- [ ] Train/test window definition
- [ ] Parameter selection without lookahead
- [ ] Per-window results
- [ ] Aggregate OOS metrics
- [ ] Cost inclusion in OOS
- [ ] Data hash verification

## Verdict

**NO_WALK_FORWARD_EVIDENCE** — Cannot produce LIVE_CANDIDATE without walk-forward.

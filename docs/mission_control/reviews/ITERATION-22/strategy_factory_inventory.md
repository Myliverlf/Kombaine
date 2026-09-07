# Strategy Factory Inventory — Iteration 22

## Existing Families (from registry)

| Family ID | Instruments | Count | Status |
|-----------|-------------|-------|--------|
| atr_breakout | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| atr_trailing_stop | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| bollinger_reversion | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| cci_channel_breakout | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| donchian_breakout | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| dual_ma_adx_filter | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| ft_bband_rsi | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| ft_binhv45 | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| ft_multi_rsi | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| nateemma_basket_meanrev | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| nfi_trend | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| rsi_reversal | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| sma_cross | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| volatility_squeeze | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| vwap_bands | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |
| vwap_reversion | BR, GAZP, LKOH, SBER, Si | varies | ACTIVE |

**Total unique families:** 16 in registry, 40 in experiment memory

## Parameter Spaces
Parameter spaces are defined per-family in the registry. Each family has:
- Instrument-specific parameterization
- Timeframe-specific configuration
- Quality gate metrics (sharpe, profit_factor, win_rate, drawdown)

## How Daily Experiment Plans Are Created
1. Universe: [BR, GAZP, LKOH, SBER, Si] × timeframes
2. Family inventory: 16 active families with parameter spaces
3. Parameter expansion: cartesian product of parameter schema
4. Experiment Memory: classify each candidate (NEW/EXACT_DUPLICATE/REVALIDATION)
5. Novelty Gate: auto-skip EXACT_DUPLICATE, RUN everything else
6. Budget: 250 candidates/day, 30% exploration / 40% revalidation / 30% neighborhood
7. Deterministic: same inputs → same plan

## Duplicate Prevention
- ExperimentMemory (SQLite) stores all prior experiment instances
- classify_candidate() checks exact instance match first
- EXACT_DUPLICATE → auto-skip via NoveltyGate
- REVALIDATION → RUN (re-test known evidence)
- Forced reproduction available via --force-reproduction flag

## Novelty Classification
Classifications: NEW, EXACT_DUPLICATE, REVALIDATION, METHODOLOGY_CHANGE, CODE_CHANGE, COST_MODEL_CHANGE, INCOMPARABLE

## Candidates Planned Per Day
Default budget: 250 candidates/day
Split: 30% exploration (75) / 40% revalidation (100) / 30% neighborhood (75)

## Backtest Execution
Backtests run through the canonical research pipeline (PipelineCoordinator).
Walk-forward semantics: train/test split with strict temporal separation.
No lookahead: test window starts after train window ends.

# Data Alignment — Iteration 15

## Source Data
- Location: `/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/`
- Format: CSV with columns: time, open, high, low, close, volume
- Timeframe: 15m (primary), 1h (secondary)
- Instruments: BR, GAZP, LKOH, SBER, Si

## Dataset Identity
- Each regime build records: instruments, timeframes, source ranges, file paths
- Observations tagged with regime_build_id
- Different builds are distinguishable, not silently joined

## Alignment Rules
1. Instrument match required
2. Timeframe match required
3. Timestamp comparison uses normalized ISO format
4. Staleness check: > 48h gap → no regime assignment
5. Different build IDs → separate evidence, not merged

## Compatibility with Existing Evidence
- analytics.db trades: ts_open/ts_close matched to regime timestamps
- Experiment memory: regime observations tagged with build_id
- Research knowledge: regime findings link to build_id + policy_version

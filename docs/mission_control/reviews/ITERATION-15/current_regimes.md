# Current Regime Snapshot — Iteration 15

## Snapshot

The current regime snapshot is obtained via `get_current_regime(store, instrument, timeframe)`.

Each snapshot contains:
- instrument
- timeframe
- trend_state
- volatility_state
- stress_state
- confidence
- as_of (timestamp of observation)
- data_freshness (FRESH / STALE / UNKNOWN)
- regime_build_id

## Freshness Policy

| Timeframe | Max Age |
|-----------|---------|
| 15m | 4 hours |
| 1h | 24 hours |

## Access Pattern

```python
from core.market_regime import RegimeStore, get_current_regime

store = RegimeStore()
snapshot = get_current_regime(store, "BR", "15m")
# Returns CurrentRegimeSnapshot with freshness check
```

## Limitations

- Snapshot is observational, not predictive
- Stale data → data_freshness = "STALE"
- No data → confidence = INSUFFICIENT

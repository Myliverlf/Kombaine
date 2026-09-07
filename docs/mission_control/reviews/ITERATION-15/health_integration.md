# Health Integration — Iteration 15

## Regime Health Check

```python
store = RegimeStore()
store.is_db_accessible()  # → True/False
```

## Health Dimensions

| Dimension | Check |
|-----------|-------|
| DB readable | `is_db_accessible()` |
| Latest build | `get_latest_build()` |
| Data freshness | `get_current_regime()` → data_freshness |
| Policy version | Build metadata |
| Coverage | Instruments with observations |
| Confidence | Distribution across observations |

## Health States

| State | Meaning |
|-------|---------|
| HEALTHY | DB accessible, recent build, fresh data, good coverage |
| DEGRADED | DB accessible but stale or low coverage |
| STALE | Data older than freshness policy |
| BLOCKED | DB inaccessible |
| UNKNOWN | Cannot determine |

# Iteration 02 — Changed Files

## Runtime safety fixes

### `core/config.py`

- `CombineConfig` now exposes `paper_first` from runtime config.
- No config value was changed.

### `core/engine.py`

1. Added final `_execution_authorized()` boundary in `Engine.post()`.
   - only `mode == "live"` and `paper_first is False` can reach `client.orders.post_order()`;
   - all current safe modes return `VETO:EXECUTION_NOT_AUTHORIZED` before broker call.
2. `fetch_broker_positions()` now returns `None` for broker/API failure instead of a fake empty `{}` snapshot.
3. `tick()` reconciles a confirmed empty `{}` broker snapshot but skips reconciliation for unknown `None`.
4. `process_slot(... broker_state_known=False)` vetoes new entry with `broker_state_unknown`.
5. Added deterministic entry `intent_id` material passed to `post()`. It is evidence for stable logical identity, not a complete durable intent journal.

### `code/validate_allocator_dryrun.py`

- Corrected analytics path from empty `state/analytics.db` to canonical root `analytics.db`.

## Tests

### `tests/test_execution_truth.py`

Added fault-injection tests for:

- final paper/live boundary with fake broker;
- zero broker calls in current paper config;
- risk-independent final mode veto;
- stable intent identity;
- broker fetch error `None` versus confirmed empty `{}`;
- reconciliation non-destructiveness under unknown state;
- canonical analytics location;
- Iteration 01 universe regression;
- no orphan open analytics trades in current state.

### `code/test_audit_fixes.py`

- Adapted fake `post()` signature for explicit `intent_id`.
- Corrected stale-candle test to match existing 180-minute hard-stale behavior. This test correction does not alter runtime strategy behavior.

## Evidence / backups

Backups before testing:

- `config.json.before.20260829T144452Z`
- `portfolio.json.before.20260829T144452Z`
- `analytics.db.before.20260829T144452Z`

All under `docs/mission_control/reviews/ITERATION-02/`, with SHA-256 output recorded at creation.

## Explicit non-changes

- no broker credentials changed;
- no broker order created;
- no broker position intentionally changed;
- `config.json` not changed;
- mode / `paper_first` unchanged;
- risk limits and strategy semantics unchanged;
- timers/services unchanged.

## Remaining architectural work

- durable order-intent/submitted/confirmed state;
- broker order-status / operations confirmation after ambiguous response;
- transactional recovery between broker fill, analytics and portfolio state.

These were documented as debt, not implemented.

# Iteration 02 — Source of Truth Audit

## Hierarchy verified

```text
Tinkoff broker positions / fills / money
  > state/portfolio.json operational state
  > root analytics.db local journal
  > reports / dashboards
```

## Broker versus local reconciliation

**Code:** `core/registry.py:reconcile_broker_positions()`.

| Mismatch | Current behavior | Status |
|---|---|---|
| Broker position, no local slot position | adopt into first matching local ticker slot with `reconciled=true` | VERIFIED |
| Local open position, confirmed broker `{}` | clear local `open_position`; emit reconciliation event | VERIFIED |
| Direction/quantity mismatch | correct local direction/quantity to broker snapshot | VERIFIED |
| Duplicate local slots for same broker ticker | retain first owner; clear duplicate | VERIFIED |
| Broker API error | since Iteration 02 `fetch_broker_positions()` returns `None`; reconciliation skipped; new exposure vetoed | RESULT |
| Broker fill exists, local analytics absent | adoption can create pending local trade record, but broker operations/fill history is not queried | PARTIALLY VERIFIED |
| Local analytics closed, broker position open | reconciliation adopts/retains broker position, but does not reopen/update an already closed analytics trade | PARTIALLY VERIFIED |

## Analytics authority

| Path | Fact | Decision |
|---|---|---|
| `analytics.db` at project root | `core.analytics.DB_PATH`; contains `trades`, `orders`, `slot_events`, `generator_feedback`; writers use `analytics.connect()` | canonical local analytics journal |
| `state/analytics.db` | size 0 at runtime audit | legacy/incorrect path, not authoritative |
| `code/validate_allocator_dryrun.py` | previously pointed to `state/analytics.db`; changed in Iteration 02 | now reads root canonical DB |

## Local write ordering

### Open

Broker response `executed>0` → `analytics.record_open()` → `slot.open_position` → `analytics.record_order()` → eventual atomic portfolio JSON save.

**Risk:** a crash after broker execution but before local writes requires broker reconciliation; no durable intent/submitted journal exists.

### Close

Broker response `executed>0` → `analytics.record_close()` → `analytics.record_order()` → local slot fields / `open_position=None` → eventual JSON save.

**Risk:** a crash between these local writes is not atomically recoverable; broker remains higher authority.

## Current runtime state observed

- Config: `mode=paper`, `paper_first=true`.
- Root analytics DB: active canonical location.
- State analytics DB: 0 bytes.
- Operational portfolio: one LKOH slot, zero open positions.
- Analytics: zero open trades.

## Evidence

- `core/analytics.py`
- `core/registry.py`
- `core/engine.py`
- filesystem/db inspection in `runtime_verification.md`
- `tests/test_execution_truth.py`
- `code/test_reconcile.py`

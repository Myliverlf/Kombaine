# Iteration 02 — Execution Graph

**Status:** VERIFIED/PARTIALLY VERIFIED · 2026-08-29

## Final broker-order boundary

The only production `strategy_combine` call to `client.orders.post_order(...)` is:

```text
core/engine.py → Engine.post()
```

Research and `code/` modules do not have production broker-order calls; matches elsewhere are tests, fixtures, validators or text scans.

## Entry path — slot signal

```text
systemd combine-supervisor.timer
→ core.supervisor.main()
→ Engine.tick()
→ Engine.fetch_broker_positions()
→ registry.reconcile_broker_positions() [only when broker state is known]
→ Engine.process_slot()
→ build_signal() from closed previous bar
→ duplicate slot/ticker guards
→ RiskManager.approve_entry() → (True, "APPROVED") or (False, reason)
→ Engine.post() FINAL MODE BOUNDARY
→ client.orders.post_order() only if mode=live AND paper_first=false
→ accepted response executed>0
→ analytics.record_open()
→ local slot.open_position / positions update
→ analytics.record_order()
→ final broker reconciliation
→ atomic registry.save_portfolio()
```

### Entry classifications

| Boundary | Status | Evidence |
|---|---|---|
| Signal generation | PARTIALLY GUARDED | strategy output is computed inside `Engine.process_slot`; no external structured signal intent exists |
| Risk approval | SAFE for regular entries | `approve_entry()` returns false on every failed condition; `process_slot()` returns before `post()` |
| Broker mode | SAFE after Iteration 02 | final `Engine.post()` vetoes unless explicit `live` + `paper_first=false` |
| Duplicate local slot/ticker | PARTIALLY GUARDED | current in-memory slot/positions and ticker checks; no durable submitted-intent ledger |
| Broker confirmation | PARTIALLY GUARDED | local open write only when response says `executed>0`; no independent order-state lookup |
| Analytics/local commit | PARTIALLY GUARDED | sequential commits, not one transaction with portfolio JSON |

## Normal exit path — SL/TP/time

```text
Engine.process_slot() sees local open_position
→ calculates stop/take/time condition
→ Engine.post() FINAL MODE BOUNDARY
→ broker response executed>0
→ analytics.record_close()
→ analytics.record_order()
→ local PnL/stop fields + open_position=None
→ final reconciliation
→ atomic portfolio save
```

**Status:** PARTIALLY GUARDED. Existing code does not ask risk manager for exits, which is acceptable only as current position-management semantics. Final mode gate now blocks broker call under paper configuration.

## Forced close / eject / auto-swap path

```text
supervisor eject rule / auto-swap retry
→ Engine.force_close_slot()
→ Engine.post() FINAL MODE BOUNDARY
→ broker response executed>0
→ analytics.record_close()
→ slot.open_position=None
→ caller may remove slot after confirmed true
```

**Status:** PARTIALLY GUARDED. Iteration 01 universe gate stops foreign pending targets before `Engine()` on auto-swap path. Iteration 02 final mode gate stops all three Engine.post callers in paper mode.

## Reconciliation path

```text
Engine.fetch_broker_positions()
  → dict = confirmed broker snapshot
  → None = API/error unknown state
confirmed dict (including {})
  → registry.reconcile_broker_positions()
  → adopt broker position / clear local stale position / correct direction/qty
None
  → skip reconciliation
  → Engine.process_slot(... broker_state_known=False)
  → new exposure VETO broker_state_unknown
```

## Runtime/scheduler protection

- `combine-supervisor.timer` calls `python3 -m core.supervisor` every 15 minutes.
- `core.supervisor.main()` uses nonblocking `fcntl.flock` on `state/.supervisor.lock`; overlapping supervisor tick exits.
- `registry.save_portfolio()` writes temporary JSON then `os.replace()` atomically.
- SQLite analytics uses WAL and 15-second busy timeout.

## Known unproven / later work

- No durable state machine separating intent/submitted/confirmed fill/broker-position.
- `post()` accepts broker response synchronously but does not independently query broker order state after timeout/ambiguous response.
- Deterministic `intent_id` is passed for entry action, but broker duplicate semantics and durable intent journal are not proven.
- The current engine performs broker read queries even in paper configuration; no broker mutation occurs through final boundary.

## Evidence

- `core/engine.py`
- `core/supervisor.py`
- `core/risk.py`
- `core/registry.py`
- `tests/test_execution_truth.py`
- `tests/test_swap_universe_gate.py`
- `runtime_verification.md`

# Iteration 02 — Broker Call Inventory

**Status:** VERIFIED · 2026-08-29

## Production order call sites inside `strategy_combine`

| Origin | Call chain | Risk / mode evidence | Classification |
|---|---|---|---|
| New entry | `Engine.process_slot()` → `Engine.post()` → `client.orders.post_order()` | `RiskManager.approve_entry()` before post; final `mode/live + paper_first=false` guard | SAFE for current paper configuration |
| Normal exit | `Engine.process_slot()` SL/TP/time → `Engine.post()` → broker | no entry risk verdict by design; final mode guard | PARTIALLY GUARDED |
| Eject timeout | `supervisor.main()` → `Engine.force_close_slot()` → `Engine.post()` → broker | final mode guard; close result must execute >0 before local clear | PARTIALLY GUARDED |
| Auto-swap close / retry | `supervisor.main()` → `force_close_slot()` → `Engine.post()` → broker | Iteration 01 universe gate before pending retry; final mode guard | PARTIALLY GUARDED |

## Final boundary

```text
core/engine.py:Engine.post()
```

Iteration 02 makes it return:

```text
VETO:EXECUTION_NOT_AUTHORIZED
```

without calling `client.orders.post_order()` unless both conditions hold:

```text
cfg.mode == "live"
cfg.paper_first is False
```

Current config is `mode=paper`, `paper_first=true`.

## Read-only broker calls

| Function | Purpose | Notes |
|---|---|---|
| `Engine.fetch_broker_positions()` | portfolio snapshot / reconciliation | returns dict for confirmed result; `None` for API/unknown failure |
| `Engine._equity()` | broker portfolio/equity read | uses fallback deposit on exception |
| `live_go()` in supervisor | margin/spec read | fallback if unavailable |

## Out-of-project direct broker calls

Parent `/root/prop-desk` contains other Tinkoff order scripts (`tinkoff_strategy.py`, `si_oi_strategy.py`, `cluster_strategy.py`, `futures_lab/futures_lab.py`).

**Status:** OUT OF SCOPE / UNPROVEN. Runtime process and systemd inventory found no active process or `combine-*` service pointing to them during this iteration. They are not part of the mapped `strategy_combine` execution chain, but remain a host-level safety concern outside this authorized scope.

## Evidence

- targeted AST/text inventory of `post_order(`
- current systemd ExecStart inspection
- process inventory at Iteration 02 runtime verification
- `core/engine.py`
- `runtime_verification.md`

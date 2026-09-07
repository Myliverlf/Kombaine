# Iteration 02 — Failure Matrix

| Failure point | Current behavior before / after Iteration 02 | Risk | Safe behavior | Status |
|---|---|---|---|---|
| Signal with risk VETO | `process_slot()` returns before `post()` | unwanted new exposure | fail closed | VERIFIED |
| Risk exception/malformed result | an exception in slot processing is caught by tick; no subsequent `post()` in that invocation | no structured risk-event record | no exposure; document later trace schema | PARTIALLY VERIFIED |
| Paper/dryrun/backtest mode | **Before:** `post()` called broker directly. **After:** final VETO before client call | real order from paper path | VETO, zero broker call | RESULT |
| `live` with `paper_first=true` | **Before:** broker call possible. **After:** final VETO | paper-first bypass | VETO, zero broker call | RESULT |
| Valid explicit live | final boundary permits call only if `live` and `paper_first=false` | intentionally out of current operating mode | separate owner approval / preflight required | VERIFIED behavior, not enabled |
| API failure fetching positions | **Before:** returned `{}`, indistinguishable from confirmed empty; tick skipped reconcile because falsy then could open. **After:** returns `None`; tick skips reconcile and vetoes new entries | duplicate entry / erase local truth after error | unknown fails closed | RESULT |
| Confirmed empty broker portfolio | `{}` is now reconciled, clearing local stale position | local ghost state | broker truth wins | RESULT / tested |
| Broker rejects open | no local open write because `executed==0` | false local position | no position mutation | VERIFIED |
| Broker accepts then process crashes before local write | broker can have fill absent local; next successful snapshot can adopt position | missing trade provenance; duplicate risk if snapshot unavailable | reconciliation; durable intent state is future work | P0/P1 DEBT |
| Timeout/ambiguous response to post_order | no broker order-state lookup / durable submitted intent | retry could duplicate logical intent | deterministic entry request id now passed; broker semantics/recovery unproven | P0 DEBT |
| Duplicate same tick | supervisor `flock`, local slot/ticker guards | duplicate order | lock/local guards | PARTIALLY VERIFIED |
| Restart after submit | no durable intent/submitted record | duplicate/replay cannot be proven safe | event/intent state-machine required | P0 DEBT |
| Failed close | executed==0 keeps local `open_position`; caller does not remove slot | lost real position not silently erased | preserve state and retry/reconcile | VERIFIED |
| Close success then local crash | broker closed but analytics/portfolio may disagree | journal mismatch | broker reconciliation detects lack of position; analytics repair incomplete | P1 DEBT |
| Analytics DB path | **Before:** allocator dry-run read empty `state/analytics.db`. **After:** root `analytics.db` | false "no trades" conclusion | one canonical root path | RESULT |
| Concurrent supervisor | `fcntl.flock` rejects overlapping tick; JSON save uses replace; SQLite WAL | stale lock/no durable command lease | no parallel tick; stale-lock handling later | PARTIALLY VERIFIED |

## Explicitly not redesigned

Iteration 02 did not introduce a full order-intent state machine, broker operation polling, or transactional portfolio+analytics commit. These require a separate bounded decision because they alter execution recovery architecture.

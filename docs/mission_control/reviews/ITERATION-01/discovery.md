# Iteration 01 — Discovery

**Scope:** configured-universe gate for supervisor auto-swap / pending replacement only.  
**Status:** VERIFIED · 2026-08-29

## Canonical universe

- **Source:** `config.json` field `universe`.
- **Runtime parser:** `core/config.py:61-88` maps it to `CombineConfig.universe`.
- **Current configured roots:** `BR, GAZP, LKOH, SBER, Si`.
- **Admission identity:** configured root ticker, case-insensitive after whitespace normalization.
- **Fail-closed rule:** `None`, empty, non-string, unresolved root and broker contract alias such as `LKU6` are VETO. Alias resolution (`LKOH → LK` for broker lookup) is intentionally not universe admission.

## Complete swap / pending path before change

1. `core/supervisor.py:288-313`: best `signal_pool` candidate is scored against an open slot; delta > 250 yields a local `swap_ready` verdict in `live_review_rows`.
2. `core/supervisor.py:338-344`: active canonical registry records are collected into `canonical_candidates`.
3. `core/supervisor.py:357-394` (pre-change): best non-same-ticker candidate was selected; if current slot had an open position, `Engine.force_close_slot()` was called first. On close failure, `old_slot['swap_pending']` was written with `target_ticker` and `target_strategy`.
4. `core/supervisor.py:171-190` (pre-change): next tick retried every pending replacement after 60 seconds by constructing `Engine` and calling `force_close_slot()`.
5. `core/engine.py:370-402`: `force_close_slot()` resolves an instrument and calls `self.post()`, which invokes Tinkoff `post_order` at `core/engine.py:193-209`.

## Root cause

No universe admission occurred at any of the four swap-specific candidate/retry boundaries. The only exclusion nearby was `regime_admit(..., {'excluded': cfg.excluded})`, which is a regime gate and not a positive configured-universe whitelist. Thus a registry candidate such as `IMOEX` could become the selected replacement and be persisted as `swap_pending`; retry then called broker-facing close logic before inspecting its target.

## Existing state inspection

- A timestamped backup was created before any possible state mutation:
  `portfolio.before.20260829T141211Z.json`
- SHA-256: `37f739c97fdfbe0e0fc5119bfc2f0ef475eccf6e01f9ffb09ee6e993ac745acd`.
- At Iteration 01 execution time, current `state/portfolio.json` had no `swap_pending` entries. Therefore no persistent pending metadata was modified.
- Current slot had `open_position=null`; no broker position state was changed.

## New boundary

`core/supervisor.py:universe_admission()` is the single local predicate for all auto-swap admission checks in this iteration.

```text
candidate root → universe_admission(config.json:universe)
  ALLOW → existing swap logic
  VETO  → structured UNIVERSE_GATE log → no pending state / no Engine / no broker action
```

The gate is invoked:

- before a candidate can influence `swap_ready` selection;
- before auto-swap candidate scoring/selection;
- before any existing `swap_pending` retry and before `Engine()` construction.

## Broker bypass review

For the **auto-swap / pending path** the gate precedes all calls to `Engine.force_close_slot()` and all paths that could subsequently call `Engine.post()`.

Other engine entry/exit paths were intentionally not altered: they are outside the authorized scope.

## Evidence

- `core/config.py`
- `core/supervisor.py`
- `core/engine.py`
- `futures_lab/futures_lab.py` alias mapping
- `tests/test_swap_universe_gate.py`
- `runtime_verification.txt`
- `state_after.txt`

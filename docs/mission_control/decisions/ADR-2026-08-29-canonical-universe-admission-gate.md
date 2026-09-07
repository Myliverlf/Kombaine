# ADR — Canonical universe admission gate for auto-swap

**Status:** ACCEPTED for Iteration 01 · 2026-08-29  
**Scope:** `core/supervisor.py` auto-swap candidate selection and persisted `swap_pending` retry only.

## Problem

An out-of-universe root (`IMOEX`) was recorded as a `swap_pending` target. The retry path created an `Engine` and could call broker-facing close behavior before checking the pending target. The configured universe existed in `config.json`, but no positive allow-list was applied in the swap path.

## Decision

`config.json:universe`, parsed into `CombineConfig.universe`, is the canonical allowed-root list for the supervisor auto-swap path.

`core/supervisor.py:universe_admission()` is the local canonical predicate introduced in this iteration:

- trims and case-folds configured root identities;
- allows only an exact configured root;
- fails closed for absent, malformed, unknown and broker-contract alias values;
- does not treat broker API aliases (`LKOH → LK`) as admission aliases;
- returns structured `ALLOW`/`VETO` evidence.

The predicate is invoked before:

1. candidate selection that can cause `swap_ready`;
2. canonical swap candidate scoring/selection;
3. retry of a persisted `swap_pending`, before `Engine()` construction.

Invalid existing pending metadata is removed locally with structured `UNIVERSE_GATE` log evidence containing original metadata. It never changes `open_position`, sends a broker request or creates a replacement.

## Rejected alternatives

1. **Filter only research/registry:** insufficient; stale/corrupt pending state can bypass it.
2. **Check only before add-slot:** insufficient; a close retry could already have hit the broker.
3. **Accept broker aliases as universe identities:** rejected; root admission and broker contract resolution are separate domains. Unknown must fail closed.
4. **Globally refactor all promotion/execution routes:** out of Iteration 01 scope.

## Safety consequences

- Foreign/unknown candidate can no longer become selected for `swap_ready` or auto-swap.
- Foreign/unknown persisted target cannot invoke `force_close_slot()` in retry flow.
- Valid configured-root pending target preserves existing retry behavior.
- Existing slot/position data remains unchanged when invalid metadata is vetoed.

## Rollback

- Revert `core/supervisor.py` and `tests/test_swap_universe_gate.py` as one unit.
- Restore `state/portfolio.json` only from timestamped backup if a future state mutation is required; Iteration 01 found no live pending state to modify.
- Reversion reopens the documented P0 risk and requires owner approval.

## Evidence

`../reviews/ITERATION-01/`:

- `discovery.md`
- `tests.txt`
- `runtime_verification.txt`
- `state_after.txt`
- `portfolio.before.20260829T141211Z.json`

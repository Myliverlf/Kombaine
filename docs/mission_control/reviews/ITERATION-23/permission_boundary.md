# Permission Boundary — Iteration 23

**Date:** 2026-08-30

## Permission Classification

| Permission | Status | Description |
|-----------|--------|-------------|
| BROKER_READ | ALLOWED (if configured) | Read account, portfolio, positions, operations |
| PAPER_EXECUTE | ALLOWED (existing policy) | Execute in paper mode |
| LIVE_PREPARE | ALLOWED (plans/evidence only) | Prepare evidence, plans, envelopes |
| LIVE_EXECUTE | **DENIED** | No real order placement |
| LIVE_CANCEL | **DENIED** | No real order cancellation |

## Enforcement

### BROKER_READ
- Token exists at `~/.hermes/tinkoff.env`
- Account ID configured: `2042640199`
- Read-only methods classified in broker_method_allowlist.md
- AST guards prevent importing mutating broker methods in code/

### PAPER_EXECUTE
- Config mode: `paper` (enforced in `core/config.py` line 73-74)
- `paper_first: true` (config.json)
- `safe_modes = {"paper", "dryrun", "backtest", "test"}` — "live" NOT in safe modes
- live_order_guard.py enforces paper mode

### LIVE_PREPARE
- Controlled-live envelope proposal created (NOT activated)
- Authorization contract defined (NOT issued)
- Evidence bundles created
- ADR prepared

### LIVE_EXECUTE
- **DENIED** — No PostOrderRequest calls in code/
- AST guards prevent broker mutation imports
- Config mode assertion prevents "live" mode
- No live authorization issued

### LIVE_CANCEL
- **DENIED** — No CancelOrderRequest calls in code/
- Same AST guards as LIVE_EXECUTE
- No live authorization issued

## Runtime Guard
`code/live_order_guard.py` enforces:
1. `assert_no_broker_imports(code_dir)` — AST scan
2. `assert_paper_mode(config_dict)` — mode check
3. `assert_no_live_mutations(portfolio_path, checksum_before)` — checksum verification

## Generic Broker-Enabled Flag
A generic "broker_enabled" flag is INSUFFICIENT. Permission boundary must be explicit per-operation:
- BROKER_READ ≠ BROKER_WRITE
- PAPER ≠ LIVE
- PREPARE ≠ EXECUTE ≠ CANCEL

## Iteration 23 Runtime Permissions
```
BROKER_READ = allowed (token exists, not tested live)
PAPER_EXECUTE = existing policy (mode=paper)
LIVE_PREPARE = plans/evidence only
LIVE_EXECUTE = DENIED
LIVE_CANCEL = DENIED
```

# Execution Path Pre-Live Audit — Iteration 23

**Date:** 2026-08-30

## Future Execution Chain (No Real Order)

```
HUMAN LIVE AUTHORIZATION (future, not issued)
→ PILOT PLAN (scope, instruments, strategy)
→ SIGNAL (strategy_generator → signal_pool)
→ RISK (risk_manager → risk_scorecard → veto/approve)
→ DURABLE EXECUTION INTENT (intent recorded, not immediate)
→ BROKER ADAPTER (live_order_guard → permission check → Tinkoff API)
→ ORDER EVIDENCE RESOLUTION (PostOrderResponse → order_id)
→ FILL/PARTIAL/UNKNOWN (polling GetOrderStateRequest)
→ RECONCILIATION (broker ↔ local)
→ ATTRIBUTION (performance attribution)
```

## Module/Function Trace

| Step | Module | Function | Bypass Protection |
|------|--------|----------|-------------------|
| Signal | code/strategy_generator.py | generate_signals() | Eligibility gate |
| Risk | code/risk_scorecard.py | score_risk() | Risk gate mandatory |
| Intent | state/signal_pool.json | Durable write | Atomic write + checksum |
| Permission | code/live_order_guard.py | assert_no_broker_imports() | AST scan |
| Permission | core/config.py | load_config() | safe_modes assertion |
| Broker | Tinkoff SDK | PostOrderRequest | Permission check required |
| Fill | Tinkoff SDK | GetOrderStateRequest | Polling with timeout |
| Reconciliation | (future) | reconcile_broker_local() | Comparison + escalation |
| Attribution | code/perf_metrics.py | compute_attribution() | Post-trade only |

## Broker Order Semantics Audit

### Client/Order ID
- Tinkoff API generates order_id on PostOrderResponse
- Client order ID (client_order_id) can be set for idempotency
- Recommendation: Use deterministic client_order_id from execution intent hash

### Idempotency
- Tinkoff API does NOT guarantee idempotency on PostOrderRequest
- Same request may create duplicate orders
- **BLOCKER:** Without idempotency guarantee, duplicate order ambiguity exists

### Duplicate Submission Protection
- No server-side deduplication confirmed
- Client must implement: check order status before re-submitting
- Execution intent must track submitted state

### Timeout
- Tinkoff API has request timeout (configurable in SDK)
- Order may be accepted but not yet filled
- Must handle: timeout → UNKNOWN → query order state

### UNKNOWN State
- Order response may return UNKNOWN execution status
- Must poll GetOrderStateRequest until resolved
- Policy: UNKNOWN beyond N seconds → escalate human

### Partial Fill
- FORTS futures may partially fill
- Must track: filled_qty vs ordered_qty
- Partial fill → continue monitoring or cancel remainder

### Rejection
- Order may be rejected (insufficient margin, trading halted, etc.)
- Must log rejection reason
- Must not auto-retry without human review

### Restart Recovery
- On system restart, must check: any pending orders?
- Query GetOrdersRequest for open orders
- Reconcile with execution intents
- Detect orphaned orders

### Operation/Fill Lookup
- GetOperationsByCursorRequest for historical fills
- GetOrderStateRequest for current order status
- Must handle pagination for large histories

## Ambiguity Assessment
**BLOCKER:** Duplicate order ambiguity exists because Tinkoff API does not guarantee idempotency on PostOrderRequest. Without authoritative server-side dedup, any retry or restart creates duplicate exposure risk. This must be resolved before live trading.

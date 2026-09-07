# Broker Order Semantics — Iteration 23

**Date:** 2026-08-30

## Tinkoff Invest API Order Semantics

### PostOrderRequest (Place Order)
- **Returns:** PostOrderResponse with order_id, execution_report_status
- **Idempotency:** NOT guaranteed. Same request may create duplicate orders.
- **Client order ID:** Optional `client_order_id` field. Used for client-side tracking, NOT server-side dedup.
- **Duplicate protection:** MUST be implemented client-side.

### PostStopOrderRequest (Place Stop Order)
- **Returns:** PostStopOrderResponse with stop_order_id
- **Same idempotency issues as PostOrderRequest**

### CancelOrderRequest
- **Returns:** CancelOrderResponse
- **Idempotency:** Canceling already-canceled order returns error
- **Must handle:** Order already filled → cannot cancel

### ReplaceOrderRequest
- **Returns:** OrderState
- **Replaces:** Existing order by order_id
- **Risk:** If original order filled before replace → new order created

### GetOrderStateRequest
- **Returns:** OrderState with execution_report_status
- **Statuses:** ORDER_EXECUTION_REPORT_STATUS_*
  - FILL
  - PARTIALLY_FILLED
  - NEW
  - CANCELLED
  - REJECTED
  - UNKNOWN

### GetOrdersRequest
- **Returns:** list of open orders
- **Pagination:** Use cursor-based pagination

### GetOperationsByCursorRequest
- **Returns:** Historical operations
- **Pagination:** Cursor-based
- **Must handle:** Large histories, rate limiting

## Order Lifecycle States
```
CREATED → SUBMITTED → ACCEPTED → PARTIAL_FILL → FILL
                                  ↘ CANCELLED
                                  ↘ REJECTED
                    ↘ UNKNOWN (must poll)
```

## Critical Gaps
1. **No idempotency guarantee** — BLOCKER for safe retry/restart
2. **UNKNOWN state handling** — Must poll until resolved or timeout
3. **Partial fill handling** — Must track filled vs ordered quantity
4. **Restart recovery** — Must check open orders on startup
5. **Rate limiting** — Tinkoff API has rate limits, must handle 429 errors

## Recommendation
Before live trading:
1. Implement client-side order deduplication (intent hash → order_id tracking)
2. Implement order state polling with timeout
3. Implement restart recovery (check open orders on startup)
4. Implement rate limit handling
5. Implement partial fill tracking

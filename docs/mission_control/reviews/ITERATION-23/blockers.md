# Blockers — Iteration 23

**Date:** 2026-08-30

## Hard Blockers (Must be resolved before any live consideration)

### 1. NO_STRATEGY_ELIGIBLE
- **Status:** BLOCKED
- **Evidence:** 546 strategies, 0 eligible (active_portfolio)
- **Impact:** No strategy can enter controlled-live envelope
- **Resolution:** Strategy must pass full eligibility pipeline (walk-forward, cost model, regime evidence, lifecycle status, PAPER evidence, ranking)
- **Cannot be bypassed:** Thresholds NOT weakened

### 2. REQUIRED_HUMAN_POLICY
- **Status:** BLOCKED
- **Evidence:** config.json has risk parameters but NO explicit live risk cap
- **Impact:** System cannot define risk limits for live trading
- **Resolution:** Human owner must define: max risk per trade, max exposure, max daily loss, max drawdown, escalation thresholds
- **Cannot be bypassed:** DO NOT invent risk amounts

### 3. BROKER_TRUTH_UNKNOWN
- **Status:** BLOCKED
- **Evidence:** Token exists at ~/.hermes/tinkoff.env but no live connection established
- **Impact:** Cannot verify account identity, portfolio, positions, operations
- **Resolution:** Establish live broker connection, query account state, verify identity
- **Cannot be bypassed:** Never fabricate broker evidence

### 4. RECONCILIATION_INCOMPLETE
- **Status:** BLOCKED
- **Evidence:** Cannot reconcile broker ↔ local without broker data
- **Impact:** Position state unknown, discrepancies undetected
- **Resolution:** Complete broker reconciliation after establishing live connection
- **Cannot be bypassed:** Never assume FLAT

### 5. ORDER_DEDUP_UNPROVEN
- **Status:** BLOCKED
- **Evidence:** Tinkoff API does not guarantee idempotency on PostOrderRequest
- **Impact:** Duplicate order risk on retry/restart
- **Resolution:** Implement client-side order deduplication (intent hash → order_id tracking)
- **Cannot be bypassed:** Duplicate exposure is a hard blocker

### 6. NO_HUMAN_AUTHORIZATION
- **Status:** BLOCKED
- **Evidence:** Authorization contract defined but not issued
- **Impact:** No valid authorization for live trading
- **Resolution:** Human owner must issue explicit authorization in NEW interaction
- **Cannot be bypassed:** Agent/system/Telegram/MC cannot authorize

## Soft Blockers (Should be resolved but not hard blockers)

### 7. TELEGRAM_NOT_CONFIGURED
- **Status:** DEGRADED
- **Evidence:** No Telegram forwarding configured
- **Impact:** No delivery channel for alerts/escalation
- **Resolution:** Configure Telegram bot and test delivery
- **Can be partially mitigated:** Other escalation channels possible

### 8. LKOH_1095D_DEGRADED
- **Status:** DEGRADED
- **Evidence:** LKOH 1095d data is duplicate of 365d
- **Impact:** Strategies requiring 1095d for LKOH cannot use genuine data
- **Resolution:** Obtain genuine LKOH 1095d data or exclude LKOH from 1095d strategies
- **Can be mitigated:** LKOH still usable with 60d/365d horizons

### 9. STALE_1H_DATA
- **Status:** DEGRADED
- **Evidence:** GAZP/LKOH 1h 60d data is 26 days old
- **Impact:** Short-horizon strategies may use stale data
- **Resolution:** Refresh 1h 60d data files
- **Can be mitigated:** Not critical for 15m strategies

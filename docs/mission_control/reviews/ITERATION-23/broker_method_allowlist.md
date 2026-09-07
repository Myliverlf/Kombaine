# Broker Method Allowlist — Iteration 23

**Date:** 2026-08-30
**SDK:** tinkoff_invest v1.0.5 (tinkoff.invest v0.2.0-beta117)

## Method Classification

### READ_ONLY_ALLOWED

| Request Type | Purpose | Classification |
|-------------|---------|----------------|
| GetAccountsRequest | Account identity | READ_ONLY |
| GetPortfolioRequest | Portfolio positions | READ_ONLY |
| GetPositionsRequest | Account positions | READ_ONLY |
| GetOrdersRequest | Open orders | READ_ONLY |
| GetOrderStateRequest | Order status | READ_ONLY |
| GetStopOrdersRequest | Stop orders | READ_ONLY |
| GetCandlesRequest | Historical candles | READ_ONLY |
| GetLastPricesRequest | Last prices | READ_ONLY |
| GetLastTradesRequest | Recent trades | READ_ONLY |
| GetOrderBookRequest | Order book | READ_ONLY |
| GetTradingStatusRequest | Instrument status | READ_ONLY |
| GetFuturesMarginRequest | Margin info | READ_ONLY |
| GetMarginAttributesRequest | Margin attributes | READ_ONLY |
| GetMaxLotsRequest | Max lot calculation | READ_ONLY |
| GetOperationsByCursorRequest | Operations history | READ_ONLY |
| GetBrokerReportRequest | Broker report | READ_ONLY |
| GetAssetFundamentalsRequest | Asset fundamentals | READ_ONLY |
| GetClosePricesRequest | Close prices | READ_ONLY |
| FindInstrumentRequest | Instrument search | READ_ONLY |
| InstrumentRequest | Instrument details | READ_ONLY |
| TradingSchedulesRequest | Trading schedules | READ_ONLY |
| GetUserTariffRequest | Tariff info | READ_ONLY |
| WithdrawLimitsRequest | Withdraw limits | READ_ONLY |
| GetInfoRequest | Account info | READ_ONLY |
| SubscribeCandlesRequest | Candle stream | READ_ONLY |
| SubscribeOrderBookRequest | Order book stream | READ_ONLY |
| SubscribeTradesRequest | Trades stream | READ_ONLY |
| SubscribeInfoRequest | Info stream | READ_ONLY |
| SubscribeLastPriceRequest | Last price stream | READ_ONLY |

### MUTATING_FORBIDDEN

| Request Type | Purpose | Classification |
|-------------|---------|----------------|
| PostOrderRequest | Place order | **MUTATING** |
| PostStopOrderRequest | Place stop order | **MUTATING** |
| CancelOrderRequest | Cancel order | **MUTATING** |
| CancelStopOrderRequest | Cancel stop order | **MUTATING** |
| ReplaceOrderRequest | Replace order | **MUTATING** |
| EditFavoritesRequest | Modify favorites | **MUTATING** |
| OpenSandboxAccountRequest | Create sandbox account | **MUTATING** |
| CloseSandboxAccountRequest | Close sandbox account | **MUTATING** |
| SandboxPayInRequest | Sandbox deposit | **MUTATING** |

### UNKNOWN_FORBIDDEN

Any request type not explicitly classified above is treated as UNKNOWN_FORBIDDEN until classified.

## Runtime Guard

The `live_order_guard.py` module enforces:
1. `assert_no_broker_imports(code_dir)` — AST scan for tinkoff/broker/order imports
2. `assert_paper_mode(config_dict)` — mode must be "paper"
3. `assert_no_live_mutations(portfolio_path, checksum_before)` — portfolio checksum verification

**Mutating broker calls count in Iteration 23: 0**

# Strategy Architect 24/7 — TimesFM-powered автокомбайн

ВАЖНО: под “нейронкой” здесь понимается конкретно **TimesFM**, forecasting neural layer/model, уже установленный/интегрированный в `strategy_combine`.

Это не generic LLM loop. Это TimesFM-centered loop.

## Цель

Комбайн должен 24/7 в paper/backtest режиме:

1. Расширять universe до 15–20 активов.
2. Проверять/добивать historical data gaps.
3. Прогонять TimesFM forecast/context по активам и timeframes.
4. Давать forecast priors, regime context, signal freshness, risk hints.
5. На базе этого генерировать/ранжировать идеи стратегий.
6. Гонять backtest.
7. Считать сделки и качество.
8. Калибровать TimesFM/adaptation loop от backtest trades.
9. Обновлять signal_pool только доказанными кандидатами.
10. Давать dashboard с понятным verdict.

## Идеальный контур

```text
Universe manager
  ↓
Data gap checker / downloader
  ↓
TimesFM context loop
  ↓
Strategy architect / generator
  ↓
Backtest factory
  ↓
Walk-forward + overfit guard
  ↓
Quality gate
  ↓
TimesFM calibration / adaptation
  ↓
Risk scorecard
  ↓
Signal pool ranking
  ↓
Dashboard + feedback memory
  ↺ назад в TimesFM context loop
```

## Почему так

Если сигналы редкие, не надо ослаблять входы. Надо:

- больше активов
- больше независимых стратегий
- больше backtest feedback
- лучше TimesFM context
- жёсткий quality/risk gate

## TimesFM роль

TimesFM не ставит ордера напрямую. Она даёт:

- forecast prior
- regime prior
- signal freshness
- confidence / uncertainty
- risk hints
- context для генератора стратегий
- calibration/adaptation hints после backtest results

## Backtest-first learning

Главный источник обучения сейчас — backtest trades, потому что real trades мало.

Для каждой стратегии сохранять:

- ticker
- strategy
- timeframe
- trades
- wins
- losses
- win_rate
- total_pnl
- avg_trade
- profit_factor
- max_drawdown
- sharpe
- walk_forward_stability
- overfit_score
- risk_verdict
- decision: keep/drop/retest

## Ограничения

- NO live orders
- NO broker mutations
- NO live supervisor start
- paper/backtest/dry-run only
- max_parallel_backtests <= 2
- TimesFM advisory unless calibrated
- signal_pool only after quality/risk gates

## Acceptance

MVP считается рабочим, когда есть:

1. TimesFM-specific loop, а не generic AI описание.
2. Конфиг 15–20 asset universe target.
3. Проверка data availability.
4. Backtest factory report.
5. Calibration/adaptation feedback от backtest trades.
6. Dashboard по стратегиям.
7. `python3 -m pytest -q` PASS.
8. `python3 code/e2e_real_dryrun.py` PASS.
9. `python3 code/final_validation.py` PASS.
10. live_orders=0.

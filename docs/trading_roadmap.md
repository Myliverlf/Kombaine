# Trading Roadmap — strategy_combine

## Цель
Держать live-портфель из 2 слотов в плюсе, а research-слой — постоянно находит более сильные кандидаты под текущий режим рынка.

## Принцип
- Live не фиксируем навсегда: если live-стратегия статистически слабеет, она должна быть ротирована.
- 2-slot лимит сохраняем всегда: сначала доказанный кандидат, потом замена.
- Меняем не торгующий контур, а правила отбора, ротации и контроля качества.
- Решения принимаем по 60d backtest + regime + portfolio-aware score + свежему live-поведению.

## Live policy
1. Если live-стратегия убыточна на свежем 60d окне и/или режим ей не подходит — помечать как rotation candidate.
2. Если есть кандидат с заметно лучшим risk-adjusted score — готовить swap.
3. Если кандидат не лучше по score — live оставлять, даже если он "новый".
4. Запрещено держать в live стратегию, которая системно проигрывает текущему режиму, только из-за старой истории.

## План действий

### Phase 1 — Research quality
1. Прогонять полный search-grid по 60d окну.
2. Для каждой стратегии считать:
   - PnL % за 60 дней
   - Profit Factor
   - Sharpe
   - Max Drawdown
   - Trades / day
   - Regime fit (trend / meanrev / volatility)
3. Отбрасывать дубликаты и клоны:
   - same ticker + same strategy family + близкие params
4. Сохранять top-20 кандидатов в `signal_pool` и отдельный report.

### Phase 2 — Live candidate selection
1. Для live-кандидатов использовать не только PnL, а composite score:
   - PnL
   - PF
   - Sharpe
   - DD
   - regime match
   - overlap with current live exposures
2. Держать буфер:
   - 2 live
   - 2 backup
   - 5 watchlist
3. Кандидат должен быть:
   - прибыльный на 60d
   - не хуже по risk-adjusted score
   - не дублировать текущий live-factor

### Phase 3 — Rotation
1. Если live-стратегия уходит в минус на свежем окне — переводить в review.
2. Если regime сменился — помечать стратегию как lower priority.
3. При появлении сильнее кандидата — replace только в рамках 2-slot режима.

### Phase 4 — Data / analytics
1. Один источник правды для candidate status: `strategy_registry.json`.
2. Legacy views (`signal_pool.json`, `waitlist.json`) — только derived.
3. Хранить отдельный log для:
   - top candidates per run
   - rejected candidates and reasons
   - regime snapshots
4. Периодически pruning per-record history.

### Phase 4B — Long-history expansion
1. Собрать continuous-историю не только 60d, а минимум:
   - 90d
   - 180d
   - 365d
   - 1095d
2. Хранить данные как stitched futures series, а не как один контракт.
3. Прогонять strategy architect по нескольким окнам и сравнивать stability.
4. Новая стратегия получает live-право только если держится на нескольких окнах, а не на одном удачном 60d.
5. Ввести multi-window score:
   - PnL%
   - PF
   - Sharpe
   - Max DD
   - stability across windows
   - regime consistency

### Phase 5 — Reporting
Каждый прогон должен отдавать:
- Top-10 strategies by 60d PnL %
- Top-10 by risk-adjusted score
- Current live vs best candidate comparison
- Regime summary
- Why candidate passed / failed

## Что можно добавить позже
- Correlation / factor clustering между стратегиями
- Stability score across rolling windows
- Regime transition detector
- Candidate cooldown, чтобы не гонять один и тот же паттерн слишком часто
- Paper shadow portfolio для новых стратегий перед live

## Acceptance criteria
- Live positions remain unchanged until explicit replace decision.
- New runs show top candidates with 60d PnL% and risk metrics.
- Duplicate candidates are filtered out.
- signal_pool contains only useful, current, non-duplicate candidates.
- Reports clearly explain why candidate A beat candidate B.

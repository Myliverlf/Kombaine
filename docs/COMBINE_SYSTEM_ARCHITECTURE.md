# Торговый комбайн — архитектура, контуры и фактическое состояние

**Версия:** 2026-08-29  
**Назначение:** единое описание того, как комбайн *должен* работать, как он *фактически* устроен сейчас и где между ними есть расхождения.  
**Режим безопасности:** `config.json` сейчас содержит `mode=paper`, `paper_first=true`; это не разрешение на изменение режима или отправку сделок.

---

## 1. Что такое торговый комбайн

Торговый комбайн — не одна стратегия и не «бот, который угадывает рынок». Это последовательный контролируемый конвейер:

**Новая управляющая надстройка:** `Hermes Control Plane` управляет автономными эпизодами улучшения комбайна через evidence-first цикл. Комбайн остаётся исследовательско-торговым конвейером, но его развитие теперь планируется и проверяется отдельным control-plane слоем поверх существующих truth stores, stage machine и run contracts.

```text
Evgeniy → Hermes Control Plane → Adaptive Work Graph → existing combine pipeline
```

Правило: control-plane не заменяет существующие canonical контуры, а только выбирает гипотезы, budgets, evidence checks и escalation path.

---

## 1.1 Autonomous control-plane overlay

Автономная надстройка работает так:

```text
OBSERVE → HYPOTHESIZE → PLAN → DECOMPOSE → EXECUTE → TEST → CRITIQUE → INTEGRATE → MEASURE → LEARN → REPLAN
```

Свойства:
- Hermes — единственный владелец canonical objective, state, budgets и evidence ledger.
- L1/L2/L3 — временные роли, а не постоянная бюрократия.
- PASS допускается только по evidence contract.
- Цикл bounded: у него есть budget, deadline, stop conditions и meta-review.

**Связанные документы:**
- `docs/mission_control/architecture_reset/autonomous_control_plane.md`
- `docs/mission_control/architecture_reset/control_plane_contracts.md`
- `docs/mission_control/architecture_reset/autonomy_state_memory.md`

---

```text
DATA
  → RESEARCH
  → STRATEGY
  → BACKTEST
  → SELECTION
  → WATCHLIST
  → SIGNAL
  → RISK
  → EXECUTION
  → BROKER
  → POSITION MANAGEMENT
  → RESULT
  → ANALYTICS
```

Его задача — пройти полный путь от исследовательской гипотезы до проверяемого результата сделки:

```text
найти идею
  → проверить на истории
  → отсеять слабую
  → поставить сильную в наблюдение
  → увидеть рыночный сигнал
  → проверить риск
  → отправить заявку
  → сверить факт у брокера
  → сопровождать позицию
  → закрыть
  → записать причину и финансовый результат
```

Главный принцип: каждый уровень — независимый контрольный контур. Следующий уровень не должен обходить предыдущий.

Особенно защищённая цепочка:

```text
SIGNAL → RISK → EXECUTION → BROKER
```

`SIGNAL ≠ ORDER`: стратегия может сказать «есть точка входа», но это ещё не право открыть позицию.

---

## 2. Роли и источники истины

| Уровень | Отвечает за | Источник истины |
|---|---|---|
| DATA | Свечи, инструменты, непрерывные фьючерсные ряды | Полученные и проверенные OHLCV-файлы + источник брокера/биржи |
| RESEARCH | Генерация и проверка большого числа гипотез | Неизменяемый результат конкретного research run |
| BACKTEST | Историческая симуляция правил | Данные, параметры, издержки и код бэктеста конкретного run |
| SELECTION | Допуск кандидата дальше | Canonical strategy registry |
| WATCHLIST | Наблюдаемые стратегии и инструменты | Canonical registry; legacy views — только производные |
| SIGNAL | Расчёт условий входа/выхода на свежих данных | Strategy/engine output с timestamp и входными свечами |
| RISK | Можно ли открывать/менять риск | Risk manager + актуальное состояние портфеля |
| EXECUTION | Создание, дедупликация, контроль заявки | Исполнитель и журнал заявки |
| BROKER | Фактические позиции, исполнения, деньги | API брокера — единственная истина по реальности |
| POSITION MANAGEMENT | Стопы, тейки, выход, reconciliation | Broker position + локальная связка slot/trade |
| ANALYTICS | PnL, сделки, причины, отчёты | Фактические broker operations и `analytics.db` |

**Критично:** `portfolio.json`, charts и исследовательский synthetic PnL не доказывают реальную прибыль. Для реальной позиции/денег выше всего по доверию брокерские операции и фактический портфель брокера.

---

## 3. Нормативная архитектура по контурам

### 3.1 DATA — рыночные данные

**Задача:** предоставить корректную, свежую и воспроизводимую историю без дыр и подмены контрактов.

Для каждого допускаемого инструмента нужны:

- идентификатор инструмента / действующий контракт;
- нормализованный root тикера;
- непрерывный (stitched/continuous) ряд для длинной истории;
- отдельные окна для текущего режима и устойчивости;
- timestamp последней свечи, число строк, хэш/версия файла;
- отчёт о пропусках и протухании.

Базовый production-universe проекта:

```text
BR, GAZP, LKOH, SBER, Si
```

Исторические окна, используемые для исследования:

```text
60 / 90 / 180 / 365 / 1095 дней
```

Роль окон:

| Окно | Смысл |
|---:|---|
| 60d | Текущий режим и свежая пригодность |
| 90d / 180d | Проверка, что результат не держится на одной короткой фазе |
| 365d | Годовой режимный фильтр |
| 1095d | Длинная устойчивость и защита от локальной подгонки |

**Недопустимо:** использовать один истёкший контракт как «трёхлетнюю историю», считать протухшие свечи свежими, подмешивать чужой тикер в явно заданный universe.

---

### 3.2 RESEARCH — поиск стратегических гипотез

**Задача:** перебрать множество связок:

```text
инструмент × timeframe × стратегия × параметры × горизонт
```

Research не должен делать реальных заявок. Его результат — доказательства по кандидатам, а не торговое действие.

Целевой ежедневный объём — до **10 000 уникальных конфигураций**, но цифра не должна быть фикцией. Для каждой попытки должны быть записаны:

- run-id;
- тикер;
- timeframe;
- стратегия;
- параметры;
- данные и горизонт;
- модель издержек;
- метрики;
- причина допуска / отбраковки;
- воспроизводимый config key.

Если валидных комбинаций в доступной сетке меньше 10k или прогон остановлен ресурсным лимитом — отчёт обязан прямо указать фактическое число, а не заявлять «прогнали 10 тысяч».

---

### 3.3 BACKTEST — историческая проверка

Бэктест — фильтр между идеей и рабочим контуром.

Он обязан проверять:

- корректность индикаторов;
- точки входа и выхода;
- комиссии и проскальзывание;
- реальное значение шага цены и стоимости пункта;
- количество сделок;
- PnL, PF, win rate, Sharpe, просадку;
- look-ahead bias и утечки будущего;
- временной порядок данных;
- устойчивость кривой капитала;
- tail-период: не умерла ли стратегия в последней части истории;
- walk-forward / multi-window устойчивость.

Результат с одной synthetic позицией и капиталом `1 000 000` — это исследовательская метрика. Он не равен доходности реального портфеля пользователя.

В пользовательском отчёте прибыль всегда должна показываться так:

```text
процент на заданный депозит + абсолютные ₽ + период + PF + DD + WR + сделки + допущения по издержкам/контракту
```

---

### 3.4 SELECTION — отбор и допуск стратегии

Технически работающий код стратегии не является основанием для допуска.

Минимальный правильный фильтр кандидата:

1. данные доступны, непрерывны и свежи;
2. стратегия имеет значимое число сделок, а не 2 случайные сделки;
3. PnL положителен после комиссий/проскальзывания;
4. PF, DD, Sharpe и shape equity проходят пороги;
5. стратегия не сломалась в tail-окне;
6. результат повторяется на нескольких окнах;
7. риск/ГО укладываются в реальный размер капитала;
8. кандидат принадлежит разрешённому universe;
9. кандидат не является коррелированным дубликатом уже выбранного пула.

После допуска кандидат получает статус, например:

```text
research → paper_candidate → watchlist → active_signal_pool → swap_ready
```

`swap_ready` — рекомендация, а не автоматическое закрытие открытой позиции.

---

### 3.5 WATCHLIST — постоянное наблюдение

Watchlist — это рабочий список «инструмент + стратегия + параметры», за которыми система наблюдает в реальном времени.

Отсутствие открытой позиции здесь нормально: стратегия продолжает работать и ждать выполнения условий.

Watchlist должен хранить:

- run-id происхождения;
- тикер, стратегию, параметры, timeframe;
- quality gates и исторические метрики;
- актуальность/TTL;
- режимную совместимость;
- разрешённый статус;
- версию данных и время последней проверки.

Legacy-файлы `waitlist.json` и `signal_pool.json` допустимы только как производные представления. Единственный записываемый источник кандидатов — canonical registry.

---

### 3.6 SIGNAL — появление торгового намерения

Когда условия стратегии на свежих свечах выполнены, формируется **signal**:

```text
instrument, side, strategy, params, timeframe,
bar timestamp, entry rationale, stop/take proposal,
quality/run provenance, expiry
```

Signal обязан быть:

- привязан к конкретной последней свече;
- ограничен сроком действия;
- дедуплицирован;
- проверяем по исходным данным;
- отделён от заявки.

Запрещено: стратегия самостоятельно вызывает брокерский API.

---

### 3.7 RISK — независимое решение о допустимости

Риск-контур отвечает не на «хорошая ли стратегия», а на вопрос:

> Можно ли именно сейчас открыть/изменить именно эту позицию?

Он должен учитывать:

- размер капитала и свободный резерв;
- ГО и размер контракта;
- лимит слотов;
- открытые позиции и суммарную направленность;
- portfolio drawdown stop;
- лимит риска на сделку;
- концентрацию и корреляцию;
- режим рынка;
- возраст сигнала;
- доступность инструмента для торговли;
- возможность исполнить заявку;
- стоп/тейк и максимальное время удержания.

Результат risk manager — структурированный `ALLOW` или `VETO` с причинами. Его нельзя обходить путём прямого вызова executor/брокера.

---

### 3.8 EXECUTION — работа с заявкой

Только разрешённый risk manager сигнал может стать order intent.

Исполнитель обязан:

1. получить актуальное брокерское состояние;
2. проверить, что такой ордер уже не отправлен;
3. сформировать идемпотентный order intent;
4. отправить заявку;
5. проверить ответ API;
6. дождаться/проверить исполнение;
7. записать внешний id заявки/операции;
8. синхронизировать локальное состояние;
9. безопасно обработать API ошибки, недоступный контракт и рынок.

**Необходимо различать:**

```text
strategy intent ≠ local slot ≠ submitted order ≠ broker fill ≠ broker position
```

---

### 3.9 BROKER — факт реальной торговли

Брокер — источник истины по:

- фактическим позициям;
- денежному балансу;
- исполнениям;
- операциям;
- заявкам;
- комиссиям.

Локальная система должна регулярно делать reconciliation:

```text
broker portfolio / operations
  ↔ local portfolio slots
  ↔ analytics trades
  ↔ order journal
```

Если пользователь видит сделку у брокера, сначала считаем её существующей и ищем подтверждение в `get_operations`/позициях, а не объявляем «сделок нет» по одному локальному JSON.

---

### 3.10 POSITION MANAGEMENT — сопровождение

После открытия позиции система контролирует:

- существует ли позиция у брокера;
- её сторону и размер;
- актуальные защитные уровни;
- условия стратегии для выхода;
- time stop;
- ошибки API и недоступность инструмента;
- дубликаты и рассинхронизацию.

Закрытие также является контролируемой цепочкой:

```text
exit condition → risk/position guard → executor → broker confirmation → analytics
```

Нельзя удалить slot только потому, что локальный код «думает», что позиция закрыта: для открытой позиции нужно подтверждение брокера.

---

### 3.11 RESULT / ANALYTICS — след и разбор результата

Для каждой сделки должна восстанавливаться цепочка:

```text
почему тикер был в universe
→ каким research run он допущен
→ какая стратегия/версия/параметры
→ какие данные использовались
→ почему появился сигнал
→ что решил risk manager
→ какая заявка сформирована
→ что принял и исполнил брокер
→ как сопровождалась позиция
→ почему закрыта
→ фактический PnL и комиссии
```

Основные хранилища:

- canonical registry: происхождение и качество стратегий;
- `analytics.db`: локальный журнал сделок/событий;
- broker operations: факт исполнений и денег;
- immutable research run bundle: воспроизводимый источник top-кандидата;
- systemd/journal logs: операционная диагностика.

---

## 4. Фактическая реализация на 2026-08-29

### 4.1 DATA — реализовано частично

Есть:

- `combine-15m.timer`: обновление intraday 15m свечей;
- локальные continuous CSV в `/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/`;
- `tools/long_history_download.py` и `tools/daily_history_archiver.py`;
- горизонты 60/365/1095 поддержаны в data loader и architect.

Расхождения:

- archive job по умолчанию обрабатывает только до трёх динамически найденных root, а не гарантированно все 5 core roots;
- long-history полнота для всех core roots должна проверяться отдельным gate;
- data gap report может показывать чужие доступные тикеры, хотя конкретный run уже ограничен core universe;
- часть отчётных ключей вида `GAZP_15m_60` могла ссылаться на файл 1095d — подписи/manifest должны быть однозначными.

---

### 4.2 RESEARCH / BACKTEST — реализованы, но не production-safe как единый цикл

Основной runner:

```text
code/strategy_architect_autopilot.py
```

Он:

- берёт CSV;
- перебирает стратегии/параметры;
- запускает `futures_lab.run_backtest`;
- считает rank/portfolio/multiwindow score;
- записывает top в registry;
- создаёт cycle JSON и `latest.md`;
- не отправляет live-orders сам (`live_orders=0`).

Текущие проблемы:

1. Default `--max-params=4`: объём не соответствует цели «10k конфигураций».
2. Default `--min-trades=2`: слишком слабый порог для production-кандидата.
3. Current adapter TimesFM — `DummyTimesFMAdapter`; это не реальная независимая ML-валидация и должно маркироваться явно.
4. JSON сохраняет только top и агрегаты, но не весь подробный ledger кандидатов. Поэтому нельзя честно построить top-10 trade-by-trade кривых из произвольного прошлого run.
5. `latest.md` — общий mutable файл. Параллельные runs перезаписывали его; так в отчёт по `GAZP/SBER/LKOH` попадали `IMOEX/USDRUB`.
6. Нет единого продового ежедневного systemd research pipeline. Hermes job `strategy-architect-autopilot` существовал, но был paused/error.

---

### 4.3 SELECTION / REGISTRY — реализованы, но есть legacy-разрыв

Canonical registry:

```text
state/strategy_registry.json
```

На момент аудита в нём было:

- 546 записей;
- 29 `active_signal_pool`;
- 3 `active_watchlist`;
- 3 `waitlist`;
- 511 `rotated_out`.

Есть canonical-to-legacy экспорт:

```text
strategy_registry.json → signal_pool.json / waitlist.json
```

Расхождения:

- `core/seeder.py` читает фиксированный старый scan-файл
  `/root/prop-desk/strategies/futures_top5_20260818_v2.scan_results.json`,
  а не текущий completed architect run;
- это создаёт два источника кандидатов: свежий architect и старый seeder scan;
- audit выявил, что history pruning в registry требует отдельной проверки: потенциально mutations могли идти по временным объектам и не уменьшать canonical JSON;
- registry вырос до крупных размеров и должен иметь deterministic retention policy.

---

### 4.4 WATCHLIST / SIGNAL — реализованы частично

Есть:

- derived `signal_pool.json`;
- derived waitlist;
- `core/supervisor.py` извлекает кандидатов, проверяет regime gate и fresh signal age;
- `core/seeder.py` валидирует кандидатов на 60d/15m перед размещением в registry.

Расхождения:

- seeder использует старый источник и только 60d 15m validation;
- `signal_max_age_minutes=16` при supervisor tick раз в 15 минут: любое небольшое опоздание может выкинуть кандидат как stale;
- нет единообразного immutable `signal intent` журнала, связывающего сигнал с run-id, входной свечой, risk verdict и order intent.

---

### 4.5 RISK — реализован, но автосвап требует жёсткого safety barrier

Есть:

- `risk.max_slots=2`;
- лимиты риска, ГО, portfolio stop и eject rules;
- regime admission;
- `portfolio_aware_score()`;
- risk approval перед добавлением нового slot;
- `swap_pending` при неудачном закрытии.

Расхождения и риск:

- в `state/portfolio.json` ранее обнаруживался `swap_pending` у SBER с целью `IMOEX/vwap_bands`, хотя `IMOEX` не входит в configured universe;
- это доказывает, что universe whitelist не применяется достаточно рано и жёстко в swap path;
- retry pending swap мог запускаться на каждом supervisor tick;
- automatic close/swap не должен быть включён, пока нет подтверждённого исполнения, правильного universe gate и отдельного разрешения на live-политику.

---

### 4.6 EXECUTION / BROKER — есть контур, факт проверяется отдельно

Компоненты:

- `core/engine.py`;
- Tinkoff Invest API;
- `core/supervisor.py`;
- `analytics.db`.

Важно:

- broker API — истина по позициям и операциям;
- локальный `state/portfolio.json` — operational state, но не брокерская истина;
- `analytics.db` расположен в корне проекта, `state/analytics.db` был пустой и не должен использоваться как источник реальных результатов.

На проверке 2026-08-29:

- SBER `atr_breakout` SHORT был зафиксирован в `analytics.db` как закрытый через `eject_timeout`;
- у LKOH slot существовал, но `open_position=null`;
- открытых позиций в локальном operational state не было.

Это не отменяет необходимости при спорной ситуации проверять broker operations напрямую.

---

## 5. Сервисы и расписание

### Активные systemd таймеры

| Таймер | Частота | Назначение |
|---|---:|---|
| `combine-15m.timer` | 15 минут | обновление intraday 15m данных |
| `combine-supervisor.timer` | 15 минут | live/paper portfolio loop |
| `combine-seeder.timer` | 60 минут | валидация/засев кандидатов |

### Дополнительные Hermes jobs

| Job | Состояние на момент аудита | Назначение |
|---|---|---|
| `strategy-combine-daily-history` | enabled, 04:00 | проверка/докачка history |
| `strategy-daily-morning-report` | enabled, 09:00 | Telegram summary/charts |
| `strategy-architect-autopilot` | paused/error | research pipeline, требует замены единым scheduler |

**Проблема:** research, history, reporting и seeding не собраны в одну последовательную, locked и проверяемую ежедневную транзакцию.

---

## 6. Ключевые расхождения «должно быть / есть»

| Контур | Должно быть | Фактически | Риск |
|---|---|---|---|
| DATA | Все core roots закрыты 60/365/1095d | archiver ограничен dynamic max-roots | Неравные условия теста |
| RESEARCH | 10k reproducible configs / run | max_params=4, нет ledger всего прогона | Непонятный охват и невозможно повторить топ |
| REPORTING | Один run-id, immutable артефакты | shared `latest.md` перезаписывается | Чужие тикеры/графики в отчёте |
| SELECTION | Один canonical pipeline | seeder читает старый fixed scan | Кандидаты не соответствуют свежему research |
| WATCHLIST | Только прошедшие актуальные кандидаты | legacy views и старые источники смешиваются | Старые/нерелевантные стратегии |
| SIGNAL | signal intent + provenance | нет полноценной сквозной записи | Нельзя объяснить конкретное решение |
| RISK | Universe gate до swap/close | foreign IMOEX попал в `swap_pending` | Неверная ротация / лишний retry |
| EXECUTION | broker truth и mode safety | guard режима требует проверки/жёсткой защиты | Риск нежелательного live поведения |
| ANALYTICS | единый источник локальной аналитики | root analytics.db и пустой state/analytics.db | Ошибка диагностики |

---

## 7. Целевая production-модель ежедневного цикла

```text
04:00  DATA GATE
       ├─ core universe: BR, GAZP, LKOH, SBER, Si
       ├─ verify / refresh 60d, 365d, 1095d × 15m, 1h
       └─ create data manifest (rows, freshness, hashes)

04:20  RESEARCH PLAN
       ├─ build deterministic unique configuration plan
       ├─ target 10k configs where source grids permit
       └─ acquire exclusive run lock

04:25  BACKTEST + WALK-FORWARD
       ├─ costs/slippage enabled
       ├─ 60/90/180/365/1095d gates
       └─ candidates.jsonl: every attempt and verdict

06:00  SELECTION
       ├─ eligible candidates only
       ├─ universe / economic / stability gates
       ├─ canonical registry transaction
       └─ export derived signal pool / waitlist

06:10  PAPER PICKUP DRY-RUN
       ├─ signal → risk → executor intent
       ├─ no broker order
       └─ verify canonical evidence and safety gates

09:00  REPORT
       ├─ exact completed run-id
       ├─ top-10 detailed candidates
       ├─ individual equity curves
       └─ stated PnL%/₽, PF, DD, WR, trades, assumptions
```

Every stage produces `success`, `partial`, or `blocked`. A partial/failed run cannot silently overwrite the previous completed run.

---

## 8. Required immutable run bundle

```text
reports/strategy_architect/runs/<run-id>/
├── manifest.json
├── research_plan.json
├── candidates.jsonl
├── eligible_candidates.json
├── top10.json
├── report.md
├── charts/
│   └── top10_trade_equity.png
└── checks.json
```

`manifest.json` must include:

- arguments;
- universe;
- strategy code version / git revision where available;
- dataset paths, ranges, hashes;
- costs/slippage assumptions;
- initial capital and contract sizing assumptions;
- timeframes/horizons;
- total planned/tested/failed/eligible configurations;
- time started/finished;
- TimesFM state (`real` or `dummy`).

Global `latest` should be only an atomic pointer to a completed run folder, never a file overwritten incrementally during a run.

---

## 9. Production safety rules

1. Current `paper_first=true` remains until a separate explicit live-readiness decision.
2. No direct strategy → broker API calls.
3. No foreign ticker can enter registry active pool, signal pool, promotion, `swap_ready` or `swap_pending`.
4. Existing live/paper slots are not closed or changed by research rollout.
5. A candidate never becomes tradeable solely from raw synthetic PnL.
6. The broker is always the truth for real positions and fills.
7. A failed close must not create duplicate replacement orders.
8. Any scheduler must use a lock and a completed run-id handoff.
9. Reporting must name the exact run-id and universe shown.
10. Every real execution decision must be traceable end-to-end.

---

## 10. План восстановления без изменения торговой логики

### Phase A — факт и защита

- inspect and document every current boundary;
- add/verify paper-mode hard guard before broker execution;
- block/cancel invalid pending swaps outside configured universe without closing positions;
- back up canonical state before any mutation;
- make registry retention deterministic.

### Phase B — research truth

- introduce immutable run bundles and lock;
- persist detailed candidate ledger;
- enforce explicit core universe;
- create deterministic 10k configuration plan;
- use realistic cost / multi-window gates.

### Phase C — canonical handoff

- migrate seeder from static historical scan to completed `eligible_candidates.json`;
- preserve registry as the single source of truth;
- derive legacy views only after registry commit.

### Phase D — automation and proof

- create one daily sequential scheduler;
- verify history coverage;
- run paper E2E;
- confirm no unapproved orders and no mutation of current slots;
- generate a run-specific top-10 chart/report.

### Phase E — separate live readiness decision

Only after all previous proof is present:

- broker preflight;
- market session probe;
- reconciliation pass;
- explicit decision about whether auto-execution or auto-swap is allowed.

This phase is not part of research pipeline rollout.

---

## 11. Files and main responsibilities

| Path | Pipeline level | Responsibility |
|---|---|---|
| `config.json` | Risk / runtime | universe, paper mode, risk limits, slot cap |
| `core/engine.py` | Execution / position management | market data, broker interaction, order/position actions |
| `core/supervisor.py` | Signal / risk orchestration | engine tick, gates, promotion, review, swap decisions |
| `core/seeder.py` | Selection / watchlist | candidate intake and short validation; currently legacy source remains |
| `core/strategy_registry.py` | Selection | canonical strategy records and state transitions |
| `code/strategy_architect_autopilot.py` | Research / backtest / selection | multi-strategy scanning and research reporting |
| `code/data_loader.py` | Data | horizon-aware CSV access |
| `tools/long_history_download.py` | Data | continuous long-history download |
| `tools/daily_history_archiver.py` | Data | scheduled history coverage check/download |
| `code/daily_morning_report.py` | Analytics / reporting | daily summary generation |
| `analytics.db` | Analytics | local trade and slot event journal |
| `state/portfolio.json` | Runtime state | local slots/operational state; not broker truth |
| `state/strategy_registry.json` | Selection | canonical candidate registry |
| `state/signal_pool.json` | Watchlist | derived active candidate view |

---

## 12. Definition of done for a healthy combiner

The combiner is operationally healthy only when all are true:

- [ ] every core ticker has verified data coverage or explicit, actionable failure;
- [ ] one locked daily research run produces a complete immutable bundle;
- [ ] number of planned/tested configurations is reported honestly;
- [ ] candidate selection is multi-window, cost-aware and statistically meaningful;
- [ ] registry is the single canonical source;
- [ ] seeder/signal pool consume the latest completed eligible artifact, not a stale static file;
- [ ] no cross-universe candidates exist in any active/swap state;
- [ ] each signal has provenance, risk decision and expiry;
- [ ] every execution is idempotent and reconciled with broker state;
- [ ] each position is traceable to its research run and entry decision;
- [ ] charts/reports are run-id-specific and cannot show data from another run;
- [ ] paper E2E passes before any live execution policy is considered;
- [ ] current live positions are never modified by research changes without explicit approval.

---

## 13. Related document

The specific implementation proposal is in:

```text
docs/ADR-2026-08-29-production-research-cycle.md
```

This document is the broader architectural reference. The ADR describes the migration from the current mixed/legacy state to the production research cycle.

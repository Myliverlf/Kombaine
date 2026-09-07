# PROP-DESK START — ЧИТАТЬ ПЕРВЫМ

> **ГРАФИКИ: рисовать ТОЛЬКО через скилл `lieflat-charts` (см. раздел ГРАФИКИ внизу). Голый matplotlib — запрет.**

Ты нейросеть/агент. Ты в `/root/prop-desk/strategy_combine` (+ соседний `futures_lab`).
Прочитай ЭТОТ файл, потом `ARCHITECTURE.md` раздел 9. Дальше действуй.

## ГЛАВНЫЕ ПРАВИЛА (нарушишь = сломаны живые деньги пользователя)

1. **LIVE ЗАПРЕЩЁН.** `mode=paper` в config.json не менять. Никаких брокерских
   вызовов, ордеров, изменений реестров/конфигов/сервисов. `run_backtest(..., debug_only=True)` всегда.
2. **Живые слоты НЕ ТРОГАТЬ**: SBER SHORT и LKOH 15m LONG торгуются вживую.
3. **git reset/clean/commit НЕ делать** (strategy_combine untracked в prop-desk).
4. RAM мало (~2-4ГБ): тяжёлое гонять ПОСЛЕДОВАТЕЛЬНО, не параллельно. earlyoom убивает.
5. Каркас НЕ менять: контур «движки → приёмник → воронка → пул → forward» фиксирован.
   Улучшать можно ТОЛЬКО движки (engines/) и ДНК (engines/genome.py), не трогая гейты.

## КАК ВСЁ УСТРОЕНО (конвейер, работает как часы)

```
ДВИЖКИ ПОИСКА (engines/)                    ЕДИНЫЙ ПРИЁМНИК
  E1 зоопарк: code/strategy_architect_autopilot.py      state/engine_candidates.json
  E2 генетик: engines/genetic_engine.py  (v3)  ───────►  + state/strategy_registry.json
  E3 моментум: engines/momentum_engine.py               (автопилот пишет в реестр)
  E4 нейронка: engines/neural_engine.py  (MLP, учится на разметке
     бэктест-экономики; веса в state/neural_models/*.pt)
        │
        ▼
ВОРОНКА tools/select_stable_pool.py
  читает реестр + приёмник; геномы регистрирует engines/register.py
  считает РЕАЛЬНЫМ futures_lab.run_backtest (те же комиссии/ГО/слиппедж)
  гейты: ≥15 сделок, ГО слота ≤50% и портфеля ≤70% капитала(20к),
  ≥60% прибыльных месяцев, dd ≤35%, calmar ≥1.5, планка медианы месяца ≥1200₽
  (снижена с 3000 решением Апостола 2026-09-06: после честного аудита выяснилось,
  что 3000₽/мес на 20к недостижимо без переобучения)
  риск-сайзинг: 2% капитала на сделку, ≤3 контракта
  выход: state/stable_pool.json (accepted true/false — ЧЕСТНО, не форсировать)
        │
        ▼
ЭКЗАМЕН tools/forward_test.py — отбор --holdout-days 60 (последние 60 дней
  спрятаны), потом forward на них. Плюс tools/plot_stable_pool.py — графики.

ЕЖЕДНЕВНО в 06:00 UTC всё это гоняет scripts/canonical_daily_research.sh
(systemd: combine-research-daily.timer). Лог: /root/prop-desk/logs/combine_research_daily.log
```

## ДНК (engines/genome.py) — что эволюционирует

- Геном = `{"long":[атом...], "short":[атом...], "risk":{...}, "exits":{...}}`
- Атомы: trend/rsi/brk/adx/vol/macd/cci/bb/volcalm + v2: gap/streak/ret/nr
- signal: +1 когда ВСЕ long-атомы истинны (AND), -1 когда все short
- Рисковые гены эволюционируются вместе с сигналами (v2)
- **v3 — ВЫХОДЫ тоже ДНК** (`exits`): stop_mode = atr|trail|supertrend,
  take_mode = atr|bb|rsi|none, брейк-ивен, EMA-выход + параметры индикаторов.
  Ядро: engines/exit_engine.py; регресс: tools/test_exit_engine.py
  (пустые exits = бит-в-бит futures_lab.run_backtest). Отбор/forward/графики
  идут через единый роутер exit_engine.run_candidate.
- Имя = `ge_<sha256[:10]>` от JSON генома → детерминированно, дедуп автоматически
- Кандидаты в engine_candidates.json хранят геном ЦЕЛИКОМ — любой инструмент
  восстанавливает его через `engines/register.py::register_genomes()`

## ЭВОЛЮЦИЯ v3 (2026-09-05) — механизмы улучшения

- Island model: 3 популяции с миграцией чемпионов (защита от вырождения в клонов)
- Предки: лучшие геномы прошлых прогонов = затравка (эволюция кумулятивна)
- Честный fitness: плюс на ОБЕИХ половинах года, штраф за концентрацию
  (одна сделка >25% прибыли), дедуп по поведению (одинаковый сигнал = клон)

## АНТИ-КЛОН (2026-09-05) — комбайн ищет РАЗНЫЕ стратегии, не близнецов

Правило Апостола: не нужны клоны с одинаковым equity. Нужны НЕПОХОЖИЕ друг
на друга стратегии примерно одинакового качества. Реализовано на двух уровнях:
- **ДНК-уровень** (engines/genetic_engine.py behavior_key): строгий отпечаток
  ПОЗИЦИЙ сигнальных баров (sha256), а не только их количества. Раньше
  «братья» одной линии пролезали в финал — отсюда 45 кандидатов LKOH,
  где реально 2-3 разные стратегии.
- **Equity-уровень** (tools/dedup_candidates.py): бэктест всех кандидатов →
  жадная кластеризация по корреляции дневных доходностей (порог 0.7):
  клон семьи отбрасывается, выживает лучший по calmar. Запускается в
  ежедневном конвейере ПЕРЕД select_stable_pool. Бэкап сырого файла:
  state/engine_candidates.raw_backup.json, отчёт: engine_candidates.dedup.json.
- **Уровень пула** (tools/select_stable_pool.py CORR_MAX=0.7): даже прошедший
  дедуп кандидат не попадёт в принятый пул, если его equity коррелирует
  >=0.7 с уже выбранной стратегией. Портфель = набор НЕЗАВИСИМЫХ кривых.

## АРБИТР ВХОДОВ (tools/entry_arbiter.py, 2026-09-05) — надстройка, НЕ замена

Проблема: старый meta-фильтр на форварде резал прибыльные сделки стратегии
(IMOEX ge_3a8b94350b: raw +N ₽ → filt +0₽, kept 0/5). Ровно сценарий
«нейронка скинула плюсы стратегии».

Решение — арбитр с жёсткой защитой от вреда:
- Арбитр НЕ генерит сигналы: КОГДА входить решает стратегия. Арбитр судит
  готовую сделку: «доверяю / не доверяю».
- Признаки сделки = поток всех мнений: рынок (rsi/тренд/вола/моментум/час) +
  мнение нейросети E4 об этом баре (p_long/p_short/p_flat, согласие, уверенность)
  + ожидание старого ridge-фильтра.
- Варианты: off (ничего не режем) / nn_veto (вето только при сильном несогласии
  нейросети) / arbiter (Ridge на объединённых признаках).
- ЧЕСТНАЯ ВАЛИДАЦИЯ: сделки режутся по времени 2/3 train | 1/3 val. Вариант
  включается ТОЛЬКО если на val он СТРОГО лучше off (filt>raw) и режет не более
  70% сделок. Не доказал пользу → enabled=false, всё идёт как без фильтра.
  «off» — всегда кандидат. Арбитр не может сделать хуже по конструкции.
- Holdout 60д арбитр НЕ ВИДИТ; финальная проверка — tools/forward_test.py
  (колонка arb=).
- Артефакт: state/entry_arbiter.json. Инференс только сохранёнными весами.
- Обучение/перепроверка: python3 tools/entry_arbiter.py --holdout-days 60

## E4 НЕЙРОНКА v3 (2026-09-05) — движок, где сигналы генерит обученная модель

- engines/neural_engine.py: разметка каждого часа = честный гипотетический PnL
  входа по нашей экономике (вариации стоп/тейк: 1.5/2/24ч, 2/3/48ч, 3/4.5/96ч;
  внутри бара сначала стоп) → классы short/flat/long.
- ПРИЗНАКИ (fv3, 38 штук): импульсы, волатильность, RSI, BB, EMA, MACD, гэпы,
  серии, час дня + ОБЪЁМЫ (vwap, OBV, vol-trend, amihud) + КОНТЕКСТ РЫНКА
  (дневной таймфрейм: тренд дня, дневной RSI, дистанция до 20-дневных хаёв/лоёв,
  дневная вола — только ЗАВЕРШЁННЫЕ дни, shift(1)) + КРОСС-РЫНОК (индекс IMOEX
  как режим рынка для всех тикеров, merge_asof без будущего) + режимы
  (vol_regime, позиция в 10-дневном канале) + календарь + свечные паттерны.
- ДАННЫЕ: максимально длинные файлы (1095d где есть: CNY 12139 баров, IMOEX 12000,
  GAZP/SBER ~8300). ОТДЕЛЬНАЯ МОДЕЛЬ НА КАЖДЫЙ ТИКЕР — свои веса.
- ПРАКТИКИ РЕАЛЬНОГО ML (v3):
  * АНСАМБЛЬ (--ens 3): 3 модели с разными сидами, усреднение логитов
  * PURGE границ train/val (Лопес де Прадо): метки смотрят вперёд на max_hold
    баров — хвост train удаляется, утечка в val устранена
  * RANDOM SEARCH гиперпараметров: lr/wd/dropout сэмплируются каждый цикл
  * Метрика val — macro-F1 + logloss (не голая accuracy)
  * КАЛИБРОВКА ТЕМПЕРАТУРОЙ на val (вероятность 0.6 реально значит 60%)
  * Веса сэмплов по силе метки (большой гипотетический PnL учит сильнее)
  * LayerNorm + gradient clipping + cosine LR schedule
  * TRAIN-VAL GAP в лидерборде — явный индикатор переобучения
- Много циклов: сид × архитектура × экономика × случайные гиперпараметры.
- ЧЕСТНОСТЬ (жёсткая схема окон):
  [train .........][val][ eval-окно ][ holdout 60д ]
  train/val — обучение и ранняя остановка; eval — бэктест-сравнение циклов
  (через общий роутер run_candidate: комиссии, ГО, слип); holdout 60д НЕ ВИДИТ
  НИКТО — его проверяет только tools/forward_test.py.
- Инференс: engines/register.py регистрирует nn_<hash> в STRATEGY_FUNCS —
  дальше обычный run_backtest/гейты/forward, БЕЗ дообучения на лету.
  (формат весов v3: state_dicts[] + temp; старые v2-веса читаются автоматически)
- В пул идут только eval-плюс, calmar≥1, ≥8 сделок, медиана месяца >0.
- Прошла гейты → попадает в пул наравне с геномами. Не прошла → отброшена.

## ПРАВИЛО: БЭКТЕСТ И СРАВНЕНИЕ ПОСЛЕ КАЖДОГО ПРОГОНА (обязательно)

После КАЖДОГО цикла обучения/эволюции/прогона движка система ОБЯЗАНА:
1. Прогнать честный бэктест результата (eval-окно или run_candidate).
2. Сравнить с предыдущим лучшим прогоном: «ЧТО БЫЛО → ЧТО СТАЛО»
   (pnl, calmar, просадка, прибыльные месяцы, имя модели/генома).
3. Записать результат в историю: state/nn_leaderboard.json (для E4),
   state/engine_candidates.json (все движки), state/stable_pool.json (пул).
4. Улучшение принимается только если стало ЛУЧШЕ на честном окне;
   ухудшение → предыдущий лучший результат остаётся в best/пуле.
Это правило касается всех движков (E1–E4): не «обучили и забыли»,
а «обучили → бэктестнули → сравнили → записали».

## ЗАПУСК (точные команды)

```bash
cd /root/prop-desk/strategy_combine
export PYTHONPATH=/root/prop-desk/strategy_combine:/root/prop-desk/futures_lab

# новый цикл эволюции (seed любой, несколько сидов подряд = больше шансов):
python3 engines/genetic_engine.py --tickers CNY,IMOEX,GAZP,SBER,LKOH --gens 10 --pop 30 --islands 3 --seed 42
python3 engines/momentum_engine.py

# отбор (полный год, затем честный holdout):
python3 tools/select_stable_pool.py
cp state/stable_pool.json /tmp/stable_full.json
python3 tools/select_stable_pool.py --holdout-days 60
python3 tools/forward_test.py        # экзамен на спрятанных 60 днях
python3 tools/plot_stable_pool.py    # графики в reports/
```

## КАК ДОБАВИТЬ НОВЫЙ МЕХАНИЗМ ПОИСКА (правильный путь)

1. Новый файл `engines/<name>_engine.py` (скопируй паттерн genetic_engine.py)
2. Он обязан: искать сам, записывать кандидатов в `state/engine_candidates.json`
   (merge по имени, формат: engine/ticker/name/desc/fitness/stats [+genome для геномов])
3. Всё. Воронка select_stable_pool подхватит и проверит РЕАЛЬНЫМ бэктестом.
   НИКОГДА не менять гейты, чтобы «пропустить» своего кандидата.

## ТЕКУЩЕЕ СОСТОЯНИЕ (проверь сам, не верь на слово)

- `state/stable_pool.json` — боевой пул (accepted = прошла ли планка 1200₽/мес)
- `state/engine_candidates.json` — приёмник всех движков
- `reports/stable_pool_equity.png`, `reports/forward_test.png` — последние графики
- Цель: медиана месяца ≥1200₽ на капитал 20к при dd ≤35% (планка снижена с 3000
  решением Апостола 2026-09-06 после аудита; старая «планка ВЗЯТА 09.05 med N»
  была раздута багами — см. /root/audits/strategy_combine_astra/).

## ГРАФИКИ — ОБЯЗАТЕЛЬНЫЙ СТИЛЬ (нарушишь — переделывать)

Все графики в этом проекте рисуются в стиле скилла **`lieflat-charts`**
(`~/.hermes/skills/lieflat-charts/` — Mono: бумага #F0EFEB, чернила #1C1C1A,
карточки 24px, Inter, hairline-линии «один волосок = один день»).

Как рисовать (по убыванию предпочтения):
1. **Equity/forward графики пула** — уже настроены, просто запускай:
   `python3 tools/plot_stable_pool.py` и `python3 tools/forward_test.py`.
   Внутри они зовут `tools/lieflat_render.py::render_equity_report()` (HTML в
   стиле скилла → headless chrome --screenshot → PNG; fallback matplotlib в
   ТОЙ ЖЕ mono-палитре).
2. **Новый график по своим данным** — используй тот же мост:
   ```python
   import sys; sys.path.insert(0, "/root/prop-desk/strategy_combine/tools")
   from lieflat_render import render_equity_report
   render_equity_report(
       series=[{"name": "SBER ge_xxx", "contracts": 1,
                "points": list(zip(dates, pnl_rub))}],   # (Timestamp, ₽)
       combined={"name": "Портфель", "sub": "метрики", "points": [...]},  # или None
       title="Заголовок-вывод", out_png="reports/my_chart.png")
   ```
3. **Сложная визуализация (не equity)** — загрузи сам скилл:
   `skill_view(name='lieflat-charts')`, выбери шаблон по catalog.md
   (OHLC → F17 Candlestick, дни → F2/F3 hairline, сравнение → F1/F5 и т.д.),
   собери HTML из templates/basics-gallery.html + mono-tokens.js и отскриншоть:
   `google-chrome --headless=new --no-sandbox --screenshot=out.png --window-size=1060,H --hide-scrollbars file:///tmp/chart.html`

ЗАПРЕТЫ по графикам:
- НЕ рисовать голым matplotlib с дефолтным стилем/цветными линиями
- НЕ изобретать свою палитру — цвета только из mono-tokens (INK/PAPER/LADDER)
- НЕ менять геометрию hairline (волосок=день, подпись пика, нулевая линия)
- В Telegram отправлять сам файл (MEDIA:путь), не путь текстом

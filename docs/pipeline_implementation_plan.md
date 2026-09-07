# Implementation Plan: Полный E2E Pipeline strategy_combine

**Дата:** 2026-09-01  
**Статус:** План — нет модификаций файлов  
**Цель:** Описать полный цикл: данные → генерация → бэктест → квалификация → выбор → реестр → риск-гейты → продвижение в paper/live

---

## 0. Что уже есть и что критически не хватает

### Что построено (рабочие модули)
| Модуль | Путь | Что делает |
|---|---|---|
| Стратегический реестр | `code/strategy_registry.py` + `core/strategy_registry.py` (bridge) | Canonical source of truth, compat views (waitlist, signal_pool) |
| 9-ступенчатый stage machine | `core/stage_machine.py` | DISCOVERY → ... → LIVE_CANDIDATE, immutable evidence contracts |
| Risk Manager | `core/risk.py` | APPROVED/VETO по R1-R4, портфельные гейты |
| Regime Detector | `core/regime.py` | ADX/EMA/ATR по юниверсу, bias для генератора |
| Strategy Factory (планировщик) | `core/strategy_factory.py` | ExperimentPlanner, Family/Hypothesis каталоги |
| Research Pipeline (координатор) | `core/research_pipeline.py` | 13 стадий, lock, budget caps, daily research |
| Human Review Gate | `core/human_review.py` | Governance boundary, decision audit |
| Qualification Campaign | `code/qualification_campaign.py` | Budget 2000, walk-forward, robustness, regime, risk, paper gates |
| Strategy Lifecycle | `core/strategy_lifecycle.py` | Decay detection, revalidation, bounded recommendations |
| Strategy Seeder | `core/seeder.py` | Handoff готовых кандидатов из скана в waitlist |
| Seeder Handoff | `core/seeder_handoff.py` |桥接 research → seeder |
| Production Truth | `core/production_truth.py` | Data integrity, instrument identity, broker reconciliation |
| System Certification | `core/system_certification.py` | G1-G12 gates, T1-T24 proofs, C1-C15 chaos |
| Eligible Candidates Contract | `core/eligible_candidates_contract.py` | Immutable handoff artifact |
| Novelty Gate | `core/novelty_gate.py` | Duplicate suppression, exact match skip |
| Engine (live) | `core/engine.py` | Paper/live по 15м-свечам Tinkoff |
| Promotion State | `core/promotion/state.py` | 15-state FSM: IDEA → LIVE_ACTIVE → RETIRED |
| Staged Universe Audit | `code/staged_universe_audit.py` | Read-only audit данных по юниверсу |
| E2E Dry-Run | `code/e2e_dryrun.py` | 14/14 PASS (validates no live mutation) |
| Smoke Promoter | `code/smoke_promoter.py` | Local smoke check before advancement |

### Данные
- **Staged universe:** 8 корней (BR, GAZP, SBER, CNY, EURRUB, USDRUB, IMOEX, NG) × 2 таймфрейма (15m, 1h) × 3 горизонта (60d, 365d, 1095d) = 48 CSV файлов
- **Активный портфель:** 1 слот (LKOH_volatility_squeeze_15m, PNL +2501₽)
- **Waitlist:** ~20 кандидатов с rank_score
- **Signal Pool:** несколько стратегий с equity diversity
- **Regime:** GAZP, SBER в trend; BR, LKOH, Si в range

### Критические пробелы (что НЕТ)
1. **Нет модуля генерации стратегий (strategy_search/generator)** — pipeline генерирует гипотезы через Factory, но нет модуля, который систематически сканирует parameter space и порождает кандидатов из данных
2. **Нет обёртки бэктеста для массового сканирования** — `run_backtest` из `futures_lab` есть, но нет модуля `batch_backtest.py` который запускает parameter grid по всем корням × таймфреймам × параметрам
3. **Нет модуля ранжирования/выбора** — кандидаты попадают в registry, но нет модуля `selection.py` который ранжирует по composite score и выбирает лучших для promotion
4. **Нет оркестратора pipeline** — `canonical_daily_research.sh` запускает только research_pipeline, но не связывает: сканирование → бэктест → квалификация → выбор → регистрация → риск-гейт → продвижение
5. **Нет promotion executor** — promotion/state.py определяет FSM, но нет модуля `promotion_executor.py` который реально двигает кандидатов по стадиям
6. **Нет backtest adapter для strategy_factory** — Factory знает о стратегиях как о метаданных, но не может вызвать бэктест и получить результаты
7. **Universe mismatch** — config.json universe = 5 тикеров (BR, GAZP, LKOH, SBER, Si), qualification_policy universe = 2 (GAZP, SBER), staged universe = 8 корней без LKOH
8. **Нет cron для бэктест-скана** —只有 canonical_daily_research.sh, нет отдельного nightly backtest scan
9. **Нет feed-forward контура** — analytics.hints из daily_digest не возвращаются в генератор для informed search

---

## 1. Архитектура полного pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR (pipeline_orchestrator.py)       │
│  Единая точка входа: cron или ручной запуск                      │
│  Управляет: phase0→phase1→...→phase5 с checkpoints               │
└────────┬────────────────────────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────────────────────────┐
    │  PHASE 0: DATA DISCOVERY & VALIDATION                       │
    │  modules/data_discovery.py                                   │
    │  - inventory local CSVs (staged_universe_audit)              │
    │  - validate completeness (15m + 1h, ≥300 rows)              │
    │  - detect stale/missing data                                 │
    │  - output: validated_universe.json                           │
    └────────┬────────────────────────────────────────────────────┘
             │ validated_universe
    ┌────────┴────────────────────────────────────────────────────┐
    │  PHASE 1: STRATEGY SEARCH & GENERATION                      │
    │  modules/strategy_search.py                                  │
    │  - load strategy families (from StrategyFactory)             │
    │  - param_grid generation (per family × instrument)           │
    │  - novelty gate check (skip EXACT_DUPLICATE)                 │
    │  - output: search_plan.json (list of experiments to run)     │
    └────────┬────────────────────────────────────────────────────┘
             │ search_plan
    ┌────────┴────────────────────────────────────────────────────┐
    │  PHASE 2: BATCH BACKTEST & METRICS                           │
    │  modules/batch_backtest.py                                   │
    │  - run_backtest per experiment (from futures_lab)            │
    │  - collect: PnL, Sharpe, PF, DD, Win%, trades/day           │
    │  - walk-forward validation (OOS folds)                       │
    │  - overfit guard (parameter neighborhood stability)          │
    │  - output: backtest_results.json (all experiments)           │
    └────────┬────────────────────────────────────────────────────┘
             │ backtest_results
    ┌────────┴────────────────────────────────────────────────────┐
    │  PHASE 3: QUALIFICATION & GATES                              │
    │  modules/qualification_gates.py                              │
    │  - apply 9-stage stage_machine logic                         │
    │  - gate checks: min_trades, min_sharpe, min_PF, max_DD     │
    │  - multi-horizon qualification (60d→365d→1095d)              │
    │  - regime compatibility check                                │
    │  - risk qualification (account size, margin, liquidity)      │
    │  - cost model validation (commission + slippage)             │
    │  - output: qualified_candidates.json                         │
    └────────┬────────────────────────────────────────────────────┘
             │ qualified_candidates
    ┌────────┴────────────────────────────────────────────────────┐
    │  PHASE 4: SELECTION & RANKING                                │
    │  modules/selection.py                                        │
    │  - composite score: sharpe×w1 + pf×w2 + consistency×w3     │
    │  - portfolio-aware scoring (correlation, delta, slots)       │
    │  - equity diversity filter (deduplicate similar curves)      │
    │  - top-N selection for registry admission                    │
    │  - output: selected_for_registry.json                        │
    └────────┬────────────────────────────────────────────────────┘
             │ selected_for_registry
    ┌────────┴────────────────────────────────────────────────────┐
    │  PHASE 5: REGISTRY UPDATE & RISK GATES                       │
    │  modules/registry_gate.py                                    │
    │  - update strategy_registry.json (canonical)                 │
    │  - export compat views (waitlist.json, signal_pool.json)     │
    │  - risk gate: GO budget check, slot count, reserve           │
    │  - promotion eligibility check                               │
    │  - human review trigger (if replacement candidate found)     │
    │  - output: promotion_decisions.json                          │
    └────────┬────────────────────────────────────────────────────┘
             │ promotion_decisions
    ┌────────┴────────────────────────────────────────────────────┐
    │  PHASE 6: PROMOTION EXECUTOR                                 │
    │  modules/promotion_executor.py                               │
    │  - execute promotion decisions (paper or live)               │
    │  - move candidate through promotion FSM states               │
    │  - handle slot ejection (R1-R4 triggers)                     │
    │  - handle replacement (top candidate → slot)                 │
    │  - portfolio delta rebalancing                               │
    │  - output: execution_report.json                             │
    └─────────────────────────────────────────────────────────────┘
```

---

## 2. Фазы реализации

### Phase 0: Data Discovery & Validation (`modules/data_discovery.py`)

**Назначение:** Единая точка входа для данных. Знает где лежат CSV, валидирует их, формирует validated universe.

**Ключевые функции:**
```python
def discover_data(project_root: Path) -> ValidatedUniverse:
    """Inventory all local CSVs, validate structure, return ready universe."""

def validate_csv(path: Path, min_rows: int = 300) -> DataHealth:
    """Check: columns exist, no NaN spikes, ≥300 rows, timestamps sequential."""

def build_universe_manifest(data_dir: Path) -> UniverseManifest:
    """Cross-reference staged_universe_audit output with actual files."""
```

**Вход:** DATA_DIR = `/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data`  
**Выход:** `state/validated_universe.json` — список инструментов с health status  
**Зависимости:** `staged_universe_audit.py` (read-only), `production_truth.py` (data identity)

**Что уже есть и переиспользуется:**
- `code/staged_universe_audit.py` — audit локальных файлов
- `core/production_truth.py` — data identity и instrument registry
- `core/data/manifest.py` + `core/data/ingress_gate.py` — data ingress

**Что нужно написать:**
- Обёртка, которая: (1) собирает inventory, (2) валидирует каждый CSV, (3) формирует unified manifest с health-статусами, (4) исключает битые файлы, (5) пишет validated_universe.json

---

### Phase 1: Strategy Search & Generation (`modules/strategy_search.py`)

**Назначение:** Генерирует план экспериментов — какой family × какой instrument × какие параметры тестировать.

**Ключевые функции:**
```python
def build_search_plan(
    universe: UniverseManifest,
    strategy_families: List[StrategyFamily],
    analytics_hints: Optional[GeneratorFeedback] = None,
    regime_bias: Optional[RegimeSnapshot] = None,
    budget: int = 2000,
) -> SearchPlan:
    """Generate parameter grid, apply novelty gate, produce experiment plan."""

def generate_param_grid(family: StrategyFamily, instrument: str) -> List[Experiment]:
    """Deterministic parameter combinations per family × instrument."""

def apply_novelty_gate(plan: SearchPlan, experiment_memory: Path) -> SearchPlan:
    """Skip EXACT_DUPLICATE experiments, keep everything else."""
```

**Вход:** validated_universe, strategy families (from StrategyFactory), regime snapshot, analytics hints  
**Выход:** `state/search_plan.json` — list of `{family, instrument, timeframe, params, priority}`  
**Зависимости:** `core/strategy_factory.py` (StrategyFamily, ExperimentPlanner), `core/novelty_gate.py`, `core/regime.py`

**Что уже есть:**
- `core/strategy_factory.py` — ExperimentPlanner, StrategyFamily, HypothesisStatus
- `core/novelty_gate.py` — EXACT_DUPLICATE skip, force-reproduction
- `core/regime.py` — regime_for(), regime snapshot

**Что нужно написать:**
- Модуль который: (1) берёт families из factory, (2) генерирует param_grid (по аналогии с qualification_campaign.py), (3) фильтрует через novelty gate, (4) ранжирует по priority (regime-aligned first), (5) обрезает до budget

**Важно:** Strategy families должны включать минимум:
- `sma_cross` (SMA crossover)
- `bollinger_reversion` (Bollinger mean reversion)
- `rsi_momentum` (RSI momentum)
- `dual_ma_adx_filter` (Dual MA + ADX filter)
- `vwap_reversion` (VWAP mean reversion)
- `nfi_trend` (Net Flow Index trend)
- `ft_supertrend` (Fractal Trend Supertrend)
- `volatility_squeeze` (BB + KC squeeze)
- `nateemma_basket_meanrev` (Multi-EMA mean reversion)
- `custom family` от Pi-агентов

---

### Phase 2: Batch Backtest & Metrics (`modules/batch_backtest.py`)

**Назначение:** Массовый прогон бэктестов по плану, сбор метрик, walk-forward валидация.

**Ключевые функции:**
```python
def run_batch_backtest(
    search_plan: SearchPlan,
    data_dir: Path,
    futures_lab_root: Path,
    config: CombineConfig,
) -> BatchBacktestResults:
    """Execute all experiments, collect metrics, run walk-forward."""

def run_single_backtest(experiment: Experiment) -> BacktestResult:
    """Run one experiment via futures_lab.run_backtest, return metrics."""

def run_walk_forward(
    experiment: Experiment,
    n_folds: int = 3,
    oos_ratio: float = 0.3,
) -> WalkForwardResult:
    """Rolling OOS validation."""

def compute_overfit_score(
    is_metrics: Metrics,
    oos_metrics: Metrics,
) -> OverfitScore:
    """Compare IS vs OOS to detect overfitting."""

def compute_composite_score(metrics: Metrics) -> float:
    """Rank_score = f(sharpe, PF, consistency, trades/day, DD ratio)."""
```

**Вход:** search_plan.json, local CSV data  
**Выход:** `state/backtest_results.json` — `{experiments: [{id, metrics, walk_forward, overfit_score, composite_score}]}  
**Зависимости:** `futures_lab.run_backtest`, `futures_lab.resolve_spec`, `futures_lab.STRATEGY_FUNCS`, `core/config.py`

**Что уже есть:**
- `futures_lab.run_backtest()` — основной бэктест движок
- `code/qualification_campaign.py` — пример batch backtest с budget
- `code/autocontour_backtest.py` — fixture-driven backtest
- `code/overfit_guard.py` — (пустой, нужно реализовать)
- `code/smooth_equity_metrics.py` — equity shape analysis
- `code/equity_diversity_filter.py` — кластеризация equity curves
- `core/eligible_candidates_contract.py` — immutable handoff

**Что нужно написать:**
- `batch_backtest.py`: orchestrates mass backtest with progress tracking, checkpoint/resume, budget enforcement
- `overfit_guard.py`: IS vs OOS comparison, parameter neighborhood stability test
- `walk_forward_adapter.py`: wraps futures_lab to do rolling OOS folds

**Питание:** Прямой вызов `futures_lab.run_backtest(df, strategy_func, **params)`

---

### Phase 3: Qualification & Gates (`modules/qualification_gates.py`)

**Назначение:** Применяет 9-ступенчатую квалификацию из stage_machine к результатам бэктеста.

**Ключевые функции:**
```python
def qualify_candidates(
    backtest_results: BatchBacktestResults,
    policy: QualificationPolicy,
) -> List[QualifiedCandidate]:
    """Apply full 9-stage qualification chain."""

def check_backtest_gate(result: BacktestResult, policy: QualificationPolicy) -> GateResult:
    """BACKTEST_QUALIFIED: min_trades, min_sharpe, min_PF, max_DD."""

def check_multi_horizon_gate(result: BacktestResult) -> GateResult:
    """MULTI_HORIZON_QUALIFIED: must work on 60d+365d+1095d."""

def check_walk_forward_gate(result: WalkForwardResult) -> GateResult:
    """WALK_FORWARD_QUALIFIED: OOS folds, no lookahead."""

def check_robustness_gate(result: BacktestResult) -> GateResult:
    """ROBUSTNESS_QUALIFIED: parameter neighborhood, temporal regime."""

def check_risk_gate(result: BacktestResult, config: CombineConfig) -> GateResult:
    """RISK_QUALIFIED: account feasibility, margin, sizing."""
```

**Вход:** backtest_results.json, research_qualification_policy.json  
**Выход:** `state/qualified_candidates.json`  
**Зависимости:** `core/stage_machine.py` (QualificationStage, STAGE_ORDER), `core/eligible_candidates_contract.py`

**Что уже есть:**
- `core/stage_machine.py` — полный 9-stage FSM с evidence contracts
- `code/qualification_campaign.py` — полная квалификация с budget 2000
- `config/research_qualification_policy.json` — thresholds, cost model, stages

**Что нужно написать:**
- `qualification_gates.py`: thin wrapper который: (1) загружает policy, (2) применяет каждый gate последовательно, (3) сохраняет evidence contract для каждого прохождения, (4) отсекает reject-причины

---

### Phase 4: Selection & Ranking (`modules/selection.py`)

**Назначение:** Ранжирует прошедших квалификацию кандидатов, выбирает лучших для реестра, фильтрует дубликаты equity curves.

**Ключевые функции:**
```python
def rank_candidates(
    qualified: List[QualifiedCandidate],
    weights: ScorecardWeights,
) -> List[RankedCandidate]:
    """Composite score ranking."""

def filter_equity_diversity(
    ranked: List[RankedCandidate],
    max_per_cluster: int = 2,
) -> List[RankedCandidate]:
    """Deduplicate similar equity curves."""

def select_for_registry(
    ranked: List[RankedCandidate],
    max_new: int = 10,
    portfolio_context: PortfolioState = None,
) -> List[RegistryCandidate]:
    """Top-N with portfolio-aware adjustments."""

def compute_portfolio_aware_score(
    candidate: RankedCandidate,
    portfolio: PortfolioState,
) -> float:
    """Adjust score by correlation, delta balance, slot availability."""
```

**Вход:** qualified_candidates.json  
**Выход:** `state/selected_for_registry.json`  
**Зависимости:** `core/registry.py`, `code/equity_diversity_filter.py`, `core/risk.py` (portfolio state)

**Что уже есть:**
- `code/equity_diversity_filter.py` — кластеризация equity curves
- `core/registry.py` — portfolio state (slots, waitlist, signal_pool)
- `code/risk_allocator_scorecard.py` + `code/risk_allocator_scorecard_v2.py` — scorecard weights

**Что нужно написать:**
- `selection.py`: (1) composite scoring, (2) equity diversity filtering, (3) portfolio-aware selection, (4) max_new enforcement, (5) delta balance consideration

---

### Phase 5: Registry Update & Risk Gates (`modules/registry_gate.py`)

**Назначение:** Обновляет canonical registry, применяет риск-гейты, определяет promotion decisions.

**Ключевые функции:**
```python
def update_registry(
    selected: List[RegistryCandidate],
    registry_path: Path,
) -> RegistryUpdate:
    """Add new candidates to canonical strategy_registry.json."""

def apply_risk_gates(
    registry_update: RegistryUpdate,
    portfolio: PortfolioState,
    risk: RiskManager,
) -> List[PromotionDecision]:
    """Check GO budget, slot count, reserve, delta band."""

def check_replacement_needed(
    portfolio: PortfolioState,
    lifecycle: StrategyLifecycle,
) -> Optional[ReplacementCandidate]:
    """Check if any active slot needs ejection (R1-R4)."""

def export_compat_views(registry_path: Path) -> None:
    """Regenerate waitlist.json and signal_pool.json from registry."""

def check_delta_balance(
    portfolio: PortfolioState,
    new_candidates: List[RegistryCandidate],
) -> DeltaCheck:
    """Is portfolio net exposure within ±30% band?"""
```

**Вход:** selected_for_registry.json, current portfolio.json  
**Выход:** `state/promotion_decisions.json`, updated strategy_registry.json  
**Зависимости:** `core/strategy_registry.py`, `core/risk.py`, `core/strategy_lifecycle.py`, `core/registry.py`

**Что уже есть:**
- `core/strategy_registry.py` — canonical registry operations
- `core/risk.py` — RiskManager (R1-R4, GO budget, reserve)
- `core/strategy_lifecycle.py` — decay detection
- `core/registry.py` — compat views, portfolio state
- `core/human_review.py` — governance boundary

**Что нужно написать:**
- `registry_gate.py`: (1) registry merge (new candidates + existing), (2) risk gate enforcement, (3) replacement detection, (4) compat view export, (5) delta balance check

---

### Phase 6: Promotion Executor (`modules/promotion_executor.py`)

**Назначение:** Выполняет promotion decisions — двигает кандидатов по FSM, управляет слотами.

**Ключевые функции:**
```python
def execute_promotions(
    decisions: List[PromotionDecision],
    portfolio: PortfolioState,
    registry: StrategyRegistry,
    config: CombineConfig,
) -> ExecutionReport:
    """Execute all promotion decisions."""

def promote_to_waitlist(candidate: RegistryCandidate) -> None:
    """Move from DISCOVERY → WAITLIST with TTL."""

def promote_to_signal_pool(candidate: RegistryCandidate) -> None:
    """Move from WAITLIST → SIGNAL_POOL after fresh retest."""

def promote_to_portfolio(
    candidate: RegistryCandidate,
    portfolio: PortfolioState,
) -> None:
    """Move from SIGNAL_POOL → ACTIVE_SLOT (after beating worst slot)."""

def eject_slot(
    slot_id: str,
    reason: str,
    portfolio: PortfolioState,
) -> None:
    """Remove slot from portfolio, trigger replacement search."""

def handle_delta_rebalance(portfolio: PortfolioState) -> None:
    """Adjust portfolio toward delta-neutral if band exceeded."""

def generate_daily_digest(
    execution_report: ExecutionReport,
    portfolio: PortfolioState,
) -> str:
    """Format Telegram digest for Апостол."""
```

**Вход:** promotion_decisions.json, current portfolio state  
**Выход:** `state/execution_report.json`, updated portfolio.json  
**Зависимости:** `core/promotion/state.py` (PromotionState FSM), `core/risk.py`, `core/engine.py`, `core/analytics.py`

**Что уже есть:**
- `core/promotion/state.py` — 15-state FSM с allowed transitions
- `core/engine.py` — live execution engine
- `core/risk.py` — RiskManager
- `core/analytics.py` — analytics store
- `core/execution_journal.py` — execution audit

**Что нужно написать:**
- `promotion_executor.py`: (1) FSM transition executor, (2) slot ejection with reason, (3) replacement candidate promotion, (4) delta rebalancing logic, (5) daily digest formatter

---

## 3. Cron Jobs (оркестрация по времени)

| Job | Schedule | Модули | Описание |
|---|---|---|---|
| `nightly_backtest_scan` | 01:00 UTC | Phase 0+1+2 | Сканирование parameter space, batch backtest |
| `daily_qualification` | 06:00 UTC | Phase 3+4 | Квалификация результатов скана, выбор |
| `daily_registry_update` | 07:00 UTC | Phase 5 | Обновление реестра, риск-гейты |
| `market_open_preflight` | 09:45 MSK | Phase 6 | Pre-market: promotion, ejection, delta check |
| `regime_update` | каждые 6ч | `core/regime.py` | Пересчёт regime, bias для генератора |
| `daily_digest` | 20:00 MSK | Telegram | Отчёт дня для Апостола |
| `weekly_revalidation` | воскр 02:00 | `strategy_lifecycle.py` | Revalidation устаревших стратегий |

---

## 4. Зависимости между модулями

```
Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6
  │            │            │            │            │            │            │
  │            │            │            │            │            │            └─ analytics.py (PNL tracking)
  │            │            │            │            │            └─ human_review.py (governance)
  │            │            │            │            └─ equity_diversity_filter.py
  │            │            │            └─ stage_machine.py (evidence contracts)
  │            │            └─ futures_lab.run_backtest
  │            └─ novelty_gate.py
  └─ production_truth.py (data identity)

Feedback loop (обратная связь):
  analytics.py ──→ generator_feedback.json ──→ Phase 1 (informed search)
  strategy_lifecycle.py ──→ decay alerts ──→ Phase 5 (ejection triggers)
  regime.py ──→ regime_snapshot.json ──→ Phase 1 (regime-aligned priority)
```

---

## 5. Новые модули для написания (сводка)

| # | Модуль | Путь | Приоритет | Сложность |
|---|---|---|---|---|
| 1 | `data_discovery.py` | `modules/data_discovery.py` | HIGH | LOW |
| 2 | `strategy_search.py` | `modules/strategy_search.py` | CRITICAL | MEDIUM |
| 3 | `batch_backtest.py` | `modules/batch_backtest.py` | CRITICAL | MEDIUM |
| 4 | `overfit_guard.py` | `code/overfit_guard.py` (fix) | HIGH | MEDIUM |
| 5 | `walk_forward_adapter.py` | `modules/walk_forward_adapter.py` | HIGH | MEDIUM |
| 6 | `qualification_gates.py` | `modules/qualification_gates.py` | HIGH | LOW (wrapper) |
| 7 | `selection.py` | `modules/selection.py` | HIGH | MEDIUM |
| 8 | `registry_gate.py` | `modules/registry_gate.py` | HIGH | MEDIUM |
| 9 | `promotion_executor.py` | `modules/promotion_executor.py` | CRITICAL | HIGH |
| 10 | `pipeline_orchestrator.py` | `modules/pipeline_orchestrator.py` | CRITICAL | MEDIUM |
| 11 | `universe_config_sync.py` | `modules/universe_config_sync.py` | MEDIUM | LOW |

---

## 6. Ближайшие действия (порядок)

1. **Унифицировать universe** — привести config.json, research_qualification_policy.json, и staged_universe к одному списку (8 корней)
2. **Написать `strategy_search.py`** — генерация param_grid по family × instrument × timeframe, с novelty gate
3. **Написать `batch_backtest.py`** — mass backtest с checkpoint/resume, walk-forward integration
4. **Написать `overfit_guard.py`** — IS vs OOS comparison (файл существует пустой)
5. **Написать `selection.py`** — composite ranking + equity diversity + portfolio-aware scoring
6. **Написать `promotion_executor.py`** — FSM transition executor + slot management
7. **Написать `pipeline_orchestrator.py`** — единая точка входа для полного цикла
8. **Настроить cron jobs** — nightly scan, daily qualification, market open preflight

---

## 7. Риски и митигации

| Риск | Митигация |
|---|---|
| 0 кандидатов проходят квалификацию (текущая проблема) | Расширить parameter grid, добавить новые families, ослабить min_sharpe до 0.2 для пилота |
| Overfit на малых выборках | Walk-forward 3+ folds, parameter neighborhood stability, fresh-retest при promotion |
| RAM/CPU на nightly scan | Budget 2000 experiments, sequential execution, checkpoint/resume |
| Live orders по ошибке | E2E dry-run 14/14 PASS доказывает no-live; mode=paper_first; kill-switch по просадке |
| Legacy view divergence | Canonical registry first; compat views только через export_compat_views() |

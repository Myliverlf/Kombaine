#!/bin/bash
# Canonical Daily Research Pipeline
# Launched by: combine-research-daily.service (systemd timer, 06:00 UTC daily)
# Lock: flock on state/.research_pipeline.lock
# Mode: paper only — zero broker mutation, zero live orders
#
# FIX 2026-09-05: previously this ran core.research_pipeline.PipelineCoordinator,
# which has NO stage handlers registered -> every stage failed, daily research
# produced nothing for days. Now it runs the real working factory:
#   1) strategy_architect_autopilot.py (generation -> backtest -> registry)
#   2) tools/rerank_signal_pool.py     (dedupe + quality rerank of the pool)
set -uo pipefail

WORKDIR="/root/prop-desk/strategy_combine"
LOCKFILE="$WORKDIR/state/.research_pipeline.lock"

echo "=== [$(date -u +%Y-%m-%dT%H:%M:%SZ)] Canonical daily research START ==="

cd "$WORKDIR"

exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo '{"status": "LOCK_HELD", "message": "Another pipeline is active. Safe refusal."}'
    exit 0
fi

export PYTHONPATH="$WORKDIR:/root/prop-desk/futures_lab"

# 1) Generation + backtest + registry intake (paper only)
python3 code/strategy_architect_autopilot.py \
    --timeframes 1h \
    --horizons 60,365 \
    --initial-cash 20000 \
    --min-trades 15 \
    --top-n 20 2>&1 | tail -3
AUTOPILOT_EXIT=${PIPESTATUS[0]}

# 1.5) Engine layer — альтернативные механизмы ПОИСКА стратегий (engines/)
#      E2 genetic v3: эволюция DSL-геномов (island model, рисковые гены, предки,
#      + ВЫХОДЫ как ДНК: atr/trail/supertrend стопы, atr/bb/rsi/none тейки, брейк-ивен, EMA-выход)
#      E3 momentum: кросс-секционные дневные паттерны
#      3 цикла эволюции с разными seed — доказано: мульти-сид резко повышает шанс
#      пробить планку (каждый цикл ~1-2 мин, дёшево).
#      Все ссыпают кандидатов в state/engine_candidates.json;
#      select_stable_pool.py оценивает их РЕАЛЬНЫМ run_backtest через те же гейты.
GEN_EXITS=""
for S in $((RANDOM)) $((RANDOM + 1)) $((RANDOM + 2)); do
    python3 engines/genetic_engine.py --tickers CNY,IMOEX,GAZP,SBER,LKOH --gens 12 --pop 40 --islands 3 --seed $S 2>&1 | tail -3
    GEN_EXITS="$GEN_EXITS ${PIPESTATUS[0]}"
done
python3 engines/momentum_engine.py 2>&1 | tail -3
MOM_EXIT=${PIPESTATUS[0]}
# E4 нейронка v3: ансамбль MLP на тикер, 38 признаков (объёмы+дневной контекст+
# кросс-рынок IMOEX+режимы), purge/калибровка/F1, eval-бэктест каждого цикла + лидерборд
python3 engines/neural_engine.py --tickers CNY,IMOEX,GAZP,SBER,LKOH,Si --cycles 12 --seed0 $S --fv 3 --ens 3 2>&1 | tail -5
NEURAL_EXIT=${PIPESTATUS[0]}

# 2) Refresh meta-filter cache (ML entry filter uplift per strategy)
if [ "$AUTOPILOT_EXIT" -eq 0 ]; then
    python3 tools/rebuild_meta_cache.py 2>&1 | tail -2
    META_EXIT=${PIPESTATUS[0]}
else
    META_EXIT=skipped
fi

# 3) Pool quality rerank (promote best, demote weak, dedupe; meta-aware scoring)
if [ "$AUTOPILOT_EXIT" -eq 0 ]; then
    python3 tools/rerank_signal_pool.py 2>&1 | tail -8
    RERANK_EXIT=${PIPESTATUS[0]}
else
    RERANK_EXIT=skipped
fi

# 4) Stability-first portfolio selection (equity smoothness > raw profit)
#    АНТИ-КЛОН: перед отбором — чистка кандидатов по корреляции equity.
#    Близнецы одной генетической линии не должны забивать пул (Апостол, 2026-09-05).
if [ "$RERANK_EXIT" -eq 0 ] 2>/dev/null || [ "$AUTOPILOT_EXIT" -eq 0 ]; then
    python3 tools/dedup_candidates.py --corr-max 0.7 2>&1 | tail -4
    DEDUP_EXIT=${PIPESTATUS[0]}
    # FIX(audit): раньше DEDUP_EXIT записывался, но НЕ проверялся — отбор шёл
    # даже после падения дедупа (пул собирался из неочищенных клонов).
    if [ "$DEDUP_EXIT" -eq 0 ]; then
        python3 tools/select_stable_pool.py 2>&1 | tail -6
        STABLE_EXIT=${PIPESTATUS[0]}
    else
        echo "DEDUP FAILED (exit=$DEDUP_EXIT) — select_stable_pool SKIPPED, state/stable_pool.json не тронут"
        STABLE_EXIT=skipped_dedup_failed
    fi
else
    DEDUP_EXIT=skipped
    STABLE_EXIT=skipped
fi

# 5) АРБИТР ВХОДОВ: надстройка над стратегией. Включает фильтр ТОЛЬКО если он
# доказал пользу на честном val-разрезе (строго лучше "off"), иначе сделки идут
# как есть — арбитр не может сделать хуже по конструкции. См. START.md.
if [ "$STABLE_EXIT" -eq 0 ] 2>/dev/null; then
    python3 tools/entry_arbiter.py --holdout-days 60 2>&1 | tail -4
    ARBITER_EXIT=${PIPESTATUS[0]}
else
    ARBITER_EXIT=skipped
fi

echo "autopilot_exit=$AUTOPILOT_EXIT genetic_exits=$GEN_EXITS momentum_exit=$MOM_EXIT neural_exit=$NEURAL_EXIT meta_exit=$META_EXIT rerank_exit=$RERANK_EXIT dedup_exit=$DEDUP_EXIT stable_exit=$STABLE_EXIT arbiter_exit=$ARBITER_EXIT"
echo "=== [$(date -u +%Y-%m-%dT%H:%M:%SZ)] Canonical daily research END ==="

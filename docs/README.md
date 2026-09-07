# strategy_combine

Краткая карта проекта.

## Сейчас это умеет
- live/paper execution engine для FORTS-фьючерсов
- risk manager и live supervisor
- adaptive strategy registry с portfolio-aware replacement policy
- legacy compatibility views для waitlist / signal pool
- regression tests for reconcile / SL-TP / adaptive registry flow

## Главные файлы
- `core/supervisor.py` — live orchestrator tick
- `core/registry.py` — legacy portfolio/waitlist/signal_pool storage helpers
- `code/strategy_registry.py` — canonical strategy registry
- `code/strategy_replacement_policy.py` — portfolio-aware scoring / replacement
- `code/strategy_supervisor_flow.py` — registry wiring and legacy export helpers
- `code/test_strategy_combine_adaptive.py` — adaptive migration/regression tests

## Архитектурная заметка
Canonical source of truth for generated strategies is `strategy_registry.json`.
Legacy `waitlist.json` and `signal_pool.json` remain compatibility views during migration.

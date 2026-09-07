# План: Improve Combine Autonomy

## Контекст
Система suburban layered runtime (L1→L2→L3) зацикливается: спавнит агентов, получает текстовые ответы, не различает "сделано" и "ещё не сделано". L3 executor — 61 строка keyword-match, не выполняет реальной работы. Оркестратор не имеет convergence-детекции.

## Ключевые факты
- `core/l3_executor.py` (61 строк) — keyword scanner, не executor
- `core/layered_agent_runtime.py` (199 строк) — спавнит 3 агента, сохраняет snapshot
- `core/layered_agent_orchestrator.py` (140 строк) — граф оркестрации
- Оркестратор повторяет "spawned L1=... L2=... L3=..." — нет loop breaker
- Доказательства (evidence) генерируются из текста, не из реальных проверок

---

## Фича 1: Deterministic Evidence Collector
- файл: `core/evidence_collector.py`
- что делает: принимает module path + check command, запускает через subprocess, возвращает structured evidence dict с stdout/returncode/timestamp. Заменяет хардкод evidence из l3_executor.
- Как проверить: `cd /root/prop-desk/strategy_combine && python -c "from core.evidence_collector import collect_module_evidence; e = collect_module_evidence('core/state_router.py', ['python', '-c', 'import core.state_router; print(\"OK\")']); print(e['verdict']); assert e['verdict'] == 'PASS'"`

## Фича 2: Convergence Detector
- файл: `core/convergence.py`
- что делает: анализирует список decisions и results из оркестрации, определяет 3 состояния: CONVERGED (все evidence sufficient), STUCK (одно и то же decision >3 раза), PROGRESS (есть новые results). Возвращает enum + reason.
- Как проверить: `cd /root/prop-desk/strategy_combine && python -c "from core.convergence import detect, Status; d = detect(['spawned L1=n1 L2=n2 L3=n3']*4, []); assert d.status == Status.STUCK"`

## Фича 3: Loop Breaker для Runtime
- файл: `core/loop_breaker.py`
- что делает: встраивается в `LayeredAgentRuntime.run_single_cycle()`. Перед spawn проверяет convergence. Если STUCK — не спавнит, а возвращает статус и рекомендацию. Если CONVERGED — завершает с финальным отчётом.
- Как проверить: `cd /root/prop-desk/strategy_combine && python -c "from core.loop_breaker import should_spawn; print(should_spawn(['spawned L1=n1']*4, []))"`

## Фича 4: Structured Plan Output для L2
- файл: `code/plan.md`
- что делает: этот файл — конкретный план из 3-5 фич (<=50 строк каждая) с командами проверки. L3 получает его как input и создаёт файлы по одному.
- Как проверить: `test -s code/plan.md && echo "plan.md exists and non-empty"`

---

## Порядок зависимостей
1. Фича 1 (evidence_collector) — без зависимостей
2. Фича 2 (convergence) — без зависимостей
3. Фича 3 (loop_breaker) — зависит от фичи 2
4. Фича 4 (plan.md) — документ, не зависит от кода

## Минимальный готовый результат
Фичи 1+2: система может собирать реальные доказательства и детектить зацикливание.

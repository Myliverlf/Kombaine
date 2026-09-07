"""OSS Shortlist Registry — машиночитаемый реестр результатов researcher.

Модуль чистый: без broker/client, без сети, без записи state/.
Без внешних зависимостей (чистый Python 3.12).

Структура каждой записи:
  {name, source_url, verdict, reason, integration_target}

verdict ∈ {"adopt_idea", "reject"}:
  adopt_idea — идея/паттерн применим, импорт не обязателен (pure-python или dev-dep).
  reject — несовместим с текущим плацдармом.

Связь с плацдармом:
  - allocator_metrics.py — метрики expectancy_r/risk_penalty/regime_bonus.
  - regime_gate.py — гейт допуска по regime/vol.
  - validate_regime_gate_dryrun.py — A/B scorecard PnL↑/risk↓.
  - task.md: max live slots <=3, 1 contract per entry, RI excluded.
"""
from typing import Any, Dict, List


# ─── Shortlist entries ─────────────────────────────────────────────────

SHORTLIST: List[Dict[str, Any]] = [
    {
        "name": "quantstats",
        "source_url": "https://github.com/ranaroussi/quantstats",
        "verdict": "adopt_idea",
        "reason": (
            "Pure-python Sharpe/Sortino/max_drawdown/profit_factor — "
            "идеи применимы без runtime-импорта; реализовать в scorecard_metrics.py "
            "как zero-dep модуль для A/B scorecard PnL↑/risk↓."
        ),
        "integration_target": "scorecard_metrics.py (new module)",
    },
    {
        "name": "empyrical-reloaded",
        "source_url": "https://github.com/quantopian/empyrical",
        "verdict": "adopt_idea",
        "reason": (
            "Аналог quantstats: staple risk metrics (sharpe, sortino, max_dd, "
            "profit_factor). Аналогичная идея: реализовать zero-dep; "
            "пакет — опциональный dev-dep, не runtime."
        ),
        "integration_target": "scorecard_metrics.py (shared with quantstats idea)",
    },
    {
        "name": "meta-labeling (hudson-and-thames)",
        "source_url": "https://github.com/hudson-and-thames/research",
        "verdict": "adopt_idea",
        "reason": (
            "Meta-labeling: дополнительный классификатор поверх signal (вход/не_вход). "
            "Идея применима к regime_gate.py как㿠te-gate: binary classifier "
            "admit/deny с threshold по confidence. Реализуемо pure-python через "
            "threshold-логику на уже существующих regime_score/vol_bucket."
        ),
        "integration_target": "regime_gate.py (enhancement)",
    },
    {
        "name": "walk-forward validation",
        "source_url": "https://github.com/topics/walk-forward-optimization",
        "verdict": "adopt_idea",
        "reason": (
            "Canonical OSS-библиотеки нет; паттерн уже реализован в "
            "strategy_replacement_policy.py (rolling window, ejection). "
            "Идея: переиспользовать существующий паттерн, не добавлять зависимости."
        ),
        "integration_target": "strategy_replacement_policy.py (already implemented)",
    },
    {
        "name": "Tinkoff invest-python SDK",
        "source_url": "https://github.com/Tinkoff/invest-python",
        "verdict": "adopt_idea",
        "reason": (
            "SDK с sandbox_client.py — paper-first архитектура. "
            "Идея: подготовить архитектурный слой для paper- Trading API "
            "в config.json (sandbox=true). Не импортировать runtime."
        ),
        "integration_target": "config.json (architectural readiness)",
    },
    {
        "name": "skfolio",
        "source_url": "https://github.com/ArthurMusset/skfolio",
        "verdict": "reject",
        "reason": (
            "Весовая портфельная аллокация (markowitz-style) несовместима с "
            "ограничениями плацдарма: max live slots ≤3, 1 contract per entry. "
            "Аллокатор в candidate_allocator.py — rank-based, не weight-based."
        ),
        "integration_target": "N/A",
    },
    {
        "name": "Riskfolio-Lib",
        "source_url": "https://github.com/dcajasn/Riskfolio-Lib",
        "verdict": "reject",
        "reason": (
            "Та же проблема что skfolio: весовая оптимизация портфеля "
            "не совместима со слотным подходом ≤3 slots и 1 контрактом/вход."
        ),
        "integration_target": "N/A",
    },
    {
        "name": "ccxt",
        "source_url": "https://github.com/ccxt/ccxt",
        "verdict": "reject",
        "reason": (
            "Брокер — Tinkoff/MOEX (config.json). ccxt подключает чужих "
            "брокеров → live-order-риски и нарушение criterion no-live-broker."
        ),
        "integration_target": "N/A",
    },
    {
        "name": "vectorbt",
        "source_url": "https://github.com/polakowo/vectorbt",
        "verdict": "reject",
        "reason": (
            "Избыточен: полный backtesting framework. Плацдарм уже имеет "
            "strategy_replacement_policy (rolling window) + regime_gate (filters). "
            "Добавление vectorbt дублирует логику и тянет heavy deps (numpy, pandas)."
        ),
        "integration_target": "N/A",
    },
]


# ─── Summary function ──────────────────────────────────────────────────

def summary() -> Dict[str, Any]:
    """Сводка shortlist для тестов/валидатора.

    Возвращает:
        {
            "total": int,          — общее число записей
            "adopt_count": int,    — сколько verdict=adopt_idea
            "reject_count": int,   — сколько verdict=reject
            "adopt_names": list,   — имена adopt-записей
            "reject_names": list,  — имена reject-записей
            "integration_targets": list,  — уникальные цели интеграции
            "verdicts_valid": bool — все verdicts ∈ {adopt_idea, reject}
        }
    """
    adopt = [r for r in SHORTLIST if r["verdict"] == "adopt_idea"]
    reject = [r for r in SHORTLIST if r["verdict"] == "reject"]
    targets = list({r["integration_target"] for r in SHORTLIST})
    all_valid = all(r["verdict"] in ("adopt_idea", "reject") for r in SHORTLIST)

    return {
        "total": len(SHORTLIST),
        "adopt_count": len(adopt),
        "reject_count": len(reject),
        "adopt_names": [r["name"] for r in adopt],
        "reject_names": [r["name"] for r in reject],
        "integration_targets": sorted(targets),
        "verdicts_valid": all_valid,
    }


# ─── No-broker guard ──────────────────────────────────────────────────
_FORBIDDEN_CALLS = {"post_order", "place_order", "send_order", "submit_order", "Client"}


def _validate_no_broker() -> None:
    """Runtime check: модуль не содержит broker-вызовов (AST-based)."""
    import ast as _ast
    import pathlib
    tree = _ast.parse(pathlib.Path(__file__).read_text())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            func = node.func
            name = ""
            if isinstance(func, _ast.Name):
                name = func.id
            elif isinstance(func, _ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_CALLS:
                raise RuntimeError(f"oss_shortlist.py contains forbidden call: {name}()")


if __name__ == "__main__":
    _validate_no_broker()
    s = summary()
    print(f"OSS Shortlist: {s['total']} entries "
          f"(adopt={s['adopt_count']}, reject={s['reject_count']})")
    print(f"Verdicts valid: {s['verdicts_valid']}")
    print(f"Integration targets: {', '.join(s['integration_targets'])}")

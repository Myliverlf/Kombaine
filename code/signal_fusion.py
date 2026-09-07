"""Signal Fusion — комбинирование 2+ индикаторов в fused signal.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции чистые: Series-in → Series-out, dict-in → dict-out.

Методы комбинирования:
  - majority_vote: signal = sign(sum(signals)), 0 если равны
  - unanimous: signal = 1/-1 только если ВСЕ согласны, иначе 0
  - weighted_average: взвешенное среднее → threshold → sign
  - threshold: доля согласных ≥ threshold → sign(mean), иначе 0

Ограничения:
  - max 3 стратегии на composition (Oyamori constraint)
  - RI ticker → VETO (score = -inf)

Источники:
  - allocator_metrics.py WEIGHTS (expectancy=40, risk=35, regime=25)
  - strategy_ideas.py check_no_broker_imports (AST-guard pattern)
"""
import ast as _ast
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Ensure code/ dir on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# ─── Constants ─────────────────────────────────────────────────────────

MAX_STRATEGIES_PER_FUSION = 3
EXCLUDED_TICKERS = {"RI"}

ALLOWED_METHODS = frozenset({"majority_vote", "unanimous", "weighted_average", "threshold"})

FORBIDDEN_IMPORT_MODULES = frozenset({
    "tinkoff", "tinkoff_api", "tinkoff.invest", "tinkoff_invest",
    "futures_lab", "broker", "broker_api",
})
FORBIDDEN_NAMES = frozenset({"client", "broker", "trader", "order", "place_order"})


# ─── Data classes ──────────────────────────────────────────────────────

@dataclass
class FusionRule:
    """Правило комбинирования нескольких стратегий в один fused signal.

    strategies: имена стратегий для комбинирования (min 2, max 3).
    method: "majority_vote" | "unanimous" | "weighted_average" | "threshold".
    weights: {strategy_name: float} — веса для weighted_average.
    threshold: float 0..1 — порог для threshold mode (доля согласных).
    """
    strategies: List[str]
    method: str = "majority_vote"
    weights: Dict[str, float] = field(default_factory=dict)
    threshold: float = 0.66

    def __post_init__(self):
        if len(self.strategies) < 2:
            raise ValueError(f"FusionRule requires >=2 strategies, got {len(self.strategies)}")
        if len(self.strategies) > MAX_STRATEGIES_PER_FUSION:
            raise ValueError(
                f"FusionRule max {MAX_STRATEGIES_PER_FUSION} strategies, "
                f"got {len(self.strategies)}"
            )
        if self.method not in ALLOWED_METHODS:
            raise ValueError(
                f"Unknown fusion method '{self.method}', "
                f"allowed: {sorted(ALLOWED_METHODS)}"
            )


# ─── Core fusion functions ────────────────────────────────────────────

def _validate_signals(
    signals: Dict[str, pd.Series],
    strategies: List[str],
) -> pd.Series:
    """Проверить что все стратегии присутствуют и сигналы совпадают по индексу."""
    for s in strategies:
        if s not in signals:
            raise KeyError(f"Strategy '{s}' not found in signals dict")
    return signals[strategies[0]]


def _align_signals(
    signals: Dict[str, pd.Series],
    strategies: List[str],
) -> pd.DataFrame:
    """Выровнять сигналы по индексу в DataFrame (стратегии = колонки)."""
    frames = {s: signals[s] for s in strategies}
    df = pd.DataFrame(frames)
    df = df.fillna(0.0)
    return df


def fuse_signals(
    signals: Dict[str, pd.Series],
    rules: List[FusionRule],
) -> Dict[str, pd.Series]:
    """Сгенерировать fused signals для каждого правила.

    signals: {strategy_name: pd.Series(-1, 0, 1)} — исходные сигналы.
    rules: список FusionRule.

    Возвращает: {rule_label: pd.Series(-1, 0, 1)} — fused signals.
    """
    result: Dict[str, pd.Series] = {}

    for rule in rules:
        aligned = _align_signals(signals, rule.strategies)
        fused = _apply_method(aligned, rule)
        label = "+".join(rule.strategies) + f"_{rule.method}"
        result[label] = fused

    return result


def _apply_method(aligned: pd.DataFrame, rule: FusionRule) -> pd.Series:
    """Применить метод комбинирования к выровненным сигналам."""
    n = aligned.shape[1]

    if rule.method == "majority_vote":
        return _majority_vote(aligned)

    elif rule.method == "unanimous":
        return _unanimous(aligned)

    elif rule.method == "weighted_average":
        return _weighted_average(aligned, rule.weights, rule.strategies)

    elif rule.method == "threshold":
        return _threshold_mode(aligned, rule.threshold)

    raise ValueError(f"Unhandled method '{rule.method}'")


def _majority_vote(df: pd.DataFrame) -> pd.Series:
    """Большинство голосов: sign(sum(signals)).

    Если сумма = 0 (равное число +1 и -1), результат = 0.
    """
    row_sum = df.sum(axis=1)
    result = row_sum.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return result.astype(int)


def _unanimous(df: pd.DataFrame) -> pd.Series:
    """Все должны согласиться: результат = 1/-1 только если все колонки = 1/-1.

    Если хотя бы одна колонка = 0 или есть расхождение → результат = 0.
    """
    pos_all = (df > 0).all(axis=1)
    neg_all = (df < 0).all(axis=1)
    result = pd.Series(0, index=df.index, dtype=int)
    result[pos_all] = 1
    result[neg_all] = -1
    return result


def _weighted_average(
    df: pd.DataFrame,
    weights: Dict[str, float],
    strategies: List[str],
) -> pd.Series:
    """Взвешенное среднее → sign(weighted_sum).

    Если вес не указан → 1.0 (равный вес).
    """
    w = np.array([weights.get(s, 1.0) for s in strategies])
    w_sum = w.sum()
    if w_sum <= 0:
        return pd.Series(0, index=df.index, dtype=int)
    w_norm = w / w_sum
    weighted = df.values @ w_norm
    result = pd.Series(
        np.where(weighted > 0, 1, np.where(weighted < 0, -1, 0)),
        index=df.index,
        dtype=int,
    )
    return result


def _threshold_mode(df: pd.DataFrame, threshold: float) -> pd.Series:
    """Доля согласных ≥ threshold → sign(mean), иначе 0.

    threshold в диапазоне 0..1, 0.66 = «более 2/3».
    """
    n = df.shape[1]
    pos_ratio = (df > 0).sum(axis=1) / n
    neg_ratio = (df < 0).sum(axis=1) / n
    result = pd.Series(0, index=df.index, dtype=int)
    result[pos_ratio >= threshold] = 1
    result[neg_ratio >= threshold] = -1
    return result


# ─── RI VETO ───────────────────────────────────────────────────────────

def check_composition_excluded(strategies: List[str], excluded: Optional[set] = None) -> bool:
    """Проверить, содержит ли composition исключённые стратегии (RI и т.п.).

    Возвращает True если composition должна быть VETO'd.
    """
    exc = excluded or EXCLUDED_TICKERS
    for s in strategies:
        if s in exc:
            return True
    return False


# ─── AST guard ─────────────────────────────────────────────────────────

def check_no_broker_imports(filepath: str) -> bool:
    """AST-traversal проверка: файл не импортирует broker/client модули.

    Паттерн из strategy_ideas.py:check_no_broker_imports().
    Возвращает True если OK (нет запрещённых импортов).
    """
    if not os.path.isfile(filepath):
        return False
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()
    tree = _ast.parse(source, filename=filepath)

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                mod_name = alias.name.split(".")[0]
                if mod_name in FORBIDDEN_IMPORT_MODULES:
                    return False
        elif isinstance(node, _ast.ImportFrom):
            if node.module:
                mod_root = node.module.split(".")[0]
                if mod_root in FORBIDDEN_IMPORT_MODULES:
                    return False
        elif isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id in FORBIDDEN_NAMES:
                return False
            if isinstance(func, _ast.Attribute) and func.attr in FORBIDDEN_NAMES:
                return False
    return True


def assert_no_broker_imports(filepath: Optional[str] = None) -> None:
    """Raise если файл содержит broker-импорты. Используется как pytest fixture."""
    if filepath is None:
        filepath = os.path.join(_HERE, "signal_fusion.py")
    if not check_no_broker_imports(filepath):
        raise ImportError(f"Broker imports detected in {filepath} — VIOLATION")

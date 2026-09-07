"""Sensitivity Analyzer — OAT (One-At-a-Time) sensitivity analysis.

Модуль НЕ импортирует broker/client, НЕ пишет state/, НЕ ходит в сеть.
Все функции чистые: dict/callable-in → dict-out. stdlib-only.

Методы:
  - OAT perturbation: Δ1 weight → Δ allocator_score → sensitivity index.
  - Monotonicity check: монотонно ли меняется score при росте веса.
  - Ranking stability: сохраняется ли порядок кандидатов при perturbation.
  - Edge-case exploration: what-if при extreme weights (0, 2×).

Связь с плацдармом:
  - allocator_metrics.WEIGHTS — базовые веса allocator-скоринга.
  - allocator_metrics.allocator_score — функция скоринга кандидатов.
  - walk_forward_optimizer.generate_param_grid — variation-网格 для WFO.
  - Основной вопрос: какие компоненты (expectancy/risk/regime) наиболее
    чувствительны к изменению весов.
"""
import copy
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
# 1. Weight Perturbation
# ═══════════════════════════════════════════════════════════════════════

def perturb_weight(
    base_weights: Dict[str, float],
    key: str,
    delta_pct: float = 0.1,
) -> Dict[str, float]:
    """Создать копию весов с изменением одного ключа на ±delta_pct.

    base_weights: dict вида {"expectancy": 40, "risk": 35, "regime": 25}.
    key: ключ для perturbation.
    delta_pct: доля изменения (0.1 = ±10%).

    Возвращает: новый dict с изменённым весом и нормализацией
    (сумма = сумма base).

    Пример:
        >>> w = perturb_weight({"a": 60, "b": 40}, "a", 0.1)
        >>> w["a"] > 60
        True
        >>> sum(w.values())
        100.0
    """
    if key not in base_weights:
        return copy.deepcopy(base_weights)

    base_sum = sum(base_weights.values())
    new = copy.deepcopy(base_weights)
    new[key] = base_weights[key] * (1.0 + delta_pct)

    # Re-normalize to preserve original sum
    new_sum = sum(new.values())
    if new_sum > 0 and base_sum > 0:
        scale = base_sum / new_sum
        for k in new:
            new[k] = round(new[k] * scale, 6)

    return new


def perturb_weight_negative(
    base_weights: Dict[str, float],
    key: str,
    delta_pct: float = 0.1,
) -> Dict[str, float]:
    """Создать копию весов с уменьшением одного ключа на delta_pct.

    Аналог perturb_weight, но direction = negative.

    Пример:
        >>> w = perturb_weight_negative({"a": 60, "b": 40}, "a", 0.1)
        >>> w["a"] < 60
        True
    """
    if key not in base_weights:
        return copy.deepcopy(base_weights)

    base_sum = sum(base_weights.values())
    new = copy.deepcopy(base_weights)
    new[key] = base_weights[key] * (1.0 - delta_pct)

    new_sum = sum(new.values())
    if new_sum > 0 and base_sum > 0:
        scale = base_sum / new_sum
        for k in new:
            new[k] = round(new[k] * scale, 6)

    return new


# ═══════════════════════════════════════════════════════════════════════
# 2. OAT Sensitivity
# ═══════════════════════════════════════════════════════════════════════

def oat_sensitivity(
    base_weights: Dict[str, float],
    score_fn: Callable[[Dict[str, float]], float],
    delta_pct: float = 0.1,
) -> Dict[str, Dict[str, float]]:
    """One-At-a-Time sensitivity analysis.

    Для каждого ключа: perturb ±delta_pct, посчитать score_fn, вычислить Δ.

    base_weights: базовые веса.
    score_fn: функция-скорер: weights → float (например, lambda w: allocator_score(...)).
    delta_pct: доля perturbation (по умолчанию 0.1 = 10%).

    Возвращает dict:
    {
        "key": {
            "base_value": float,
            "perturbed_up": float,      # score при +delta_pct
            "perturbed_down": float,    # score при -delta_pct
            "delta_up": float,          # score(perturbed_up) - base_score
            "delta_down": float,        # score(perturbed_down) - base_score
            "sensitivity_index": float, # abs(delta_up - delta_down) / 2
            "monotonic": bool,          # monotonic if delta_up and delta_down have same sign pattern
        },
        ...
    }

    Пример:
        >>> base = {"a": 60, "b": 40}
        >>> result = oat_sensitivity(base, lambda w: sum(v**2 for v in w.values()))
        >>> "a" in result
        True
        >>> "sensitivity_index" in result["a"]
        True
    """
    base_score = score_fn(base_weights)
    result: Dict[str, Dict[str, float]] = {}

    for key in base_weights:
        w_up = perturb_weight(base_weights, key, delta_pct)
        w_down = perturb_weight_negative(base_weights, key, delta_pct)

        score_up = score_fn(w_up)
        score_down = score_fn(w_down)

        delta_up = score_up - base_score
        delta_down = score_down - base_score

        sensitivity = abs(delta_up - delta_down) / 2.0

        # Monotonicity: delta_up > 0 and delta_down < 0 → increasing weight increases score
        # Or delta_up < 0 and delta_down > 0 → increasing weight decreases score
        monotonic = (delta_up > 0 and delta_down < 0) or (delta_up < 0 and delta_down > 0)

        result[key] = {
            "base_value": base_weights[key],
            "perturbed_up": score_up,
            "perturbed_down": score_down,
            "delta_up": delta_up,
            "delta_down": delta_down,
            "sensitivity_index": sensitivity,
            "monotonic": monotonic,
        }

    return result


# ═══════════════════════════════════════════════════════════════════════
# 3. Sensitivity Report
# ═══════════════════════════════════════════════════════════════════════

def sensitivity_report(
    base_weights: Dict[str, float],
    score_fn: Callable[[Dict[str, float]], float],
    delta_pct: float = 0.1,
) -> Dict[str, object]:
    """Полный sensitivity report с rank и verdict.

    Возвращает dict:
    {
        "base_score": float,
        "per_weight": dict (from oat_sensitivity),
        "ranked": list of (key, sensitivity_index) sorted desc,
        "most_sensitive": str,
        "least_sensitive": str,
        "all_monotonic": bool,
        "total_sensitivity": float,
    }

    Пример:
        >>> base = {"a": 60, "b": 40}
        >>> report = sensitivity_report(base, lambda w: sum(v**2 for v in w.values()))
        >>> report["most_sensitive"] in base
        True
    """
    per_weight = oat_sensitivity(base_weights, score_fn, delta_pct)

    ranked = sorted(
        per_weight.items(),
        key=lambda x: x[1]["sensitivity_index"],
        reverse=True,
    )

    ranked_keys = [(k, v["sensitivity_index"]) for k, v in ranked]
    total_sensitivity = sum(v["sensitivity_index"] for v in per_weight.values())
    all_monotonic = all(v["monotonic"] for v in per_weight.values())

    return {
        "base_score": score_fn(base_weights),
        "per_weight": per_weight,
        "ranked": ranked_keys,
        "most_sensitive": ranked[0][0] if ranked else "",
        "least_sensitive": ranked[-1][0] if ranked else "",
        "all_monotonic": all_monotonic,
        "total_sensitivity": total_sensitivity,
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. Edge-Case Explorer (what-if extreme weights)
# ═══════════════════════════════════════════════════════════════════════

def edge_case_explorer(
    base_weights: Dict[str, float],
    score_fn: Callable[[Dict[str, float]], float],
    multipliers: Optional[List[float]] = None,
) -> List[Dict[str, object]]:
    """Explore what-if scenarios at extreme weight values.

    multipliers: список коэффициентов для каждого ключа.
                 По умолчанию [0.0, 0.5, 1.0, 1.5, 2.0].

    Возвращает: список dict[{multiplier, weights, score, delta_from_base}, ...]

    Пример:
        >>> cases = edge_case_explorer({"a": 60, "b": 40}, lambda w: sum(w.values()))
        >>> len(cases) > 0
        True
    """
    if multipliers is None:
        multipliers = [0.0, 0.5, 1.0, 1.5, 2.0]

    base_score = score_fn(base_weights)
    base_sum = sum(base_weights.values())
    cases: List[Dict[str, object]] = []

    for key in base_weights:
        for mult in multipliers:
            new = copy.deepcopy(base_weights)
            new[key] = base_weights[key] * mult

            # Re-normalize
            new_sum = sum(new.values())
            if new_sum > 0 and base_sum > 0:
                scale = base_sum / new_sum
                for k in new:
                    new[k] = round(new[k] * scale, 6)

            sc = score_fn(new)
            cases.append({
                "key": key,
                "multiplier": mult,
                "weights": copy.deepcopy(new),
                "score": sc,
                "delta_from_base": sc - base_score,
            })

    return cases

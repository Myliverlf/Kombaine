"""Idea Deduplicator — дедупликация стратегий-идей по схожести параметров.

Группирует идеи по (ticker, direction), находит «близнецов» (схожие params +
strategy_name), оставляет лучший по score. VETO-идеи (score=-inf) отбрасываются
ДО дедупликации.

Функции:
  - deduplicate_ideas(ideas, sim_threshold=0.7) → list
  - _param_similarity(a, b) → float  (Jaccard на числовых param values + exact name match)

Зависимости: stdlib only (math).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


# ─── Внутренний helper ─────────────────────────────────────────────────

def _is_veto(idea: Dict[str, Any]) -> bool:
    """True если idea VETO'd (score = -inf)."""
    score = idea.get("score")
    if score is None:
        return False
    return math.isinf(score) and score < 0


def _param_values(params: Optional[Dict[str, Any]]) -> List[float]:
    """Извлечь числовые значения из dict params для сравнения."""
    if not params:
        return []
    vals: List[float] = []
    for v in params.values():
        if isinstance(v, (int, float)):
            vals.append(float(v))
    return vals


def _param_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Схожесть двух идей по strategy_name + params.

    Возвращает 0..1:
      - exact match strategy_name → +0.5
      - Jaccard на числовых param values → +0.5 (scaled)
    """
    sim = 0.0

    # Strategy name exact match
    name_a = a.get("strategy_name", "")
    name_b = b.get("strategy_name", "")
    name_match = bool(name_a and name_b and name_a == name_b)

    # Numeric params Jaccard
    vals_a = _param_values(a.get("params"))
    vals_b = _param_values(b.get("params"))

    if name_match and not vals_a and not vals_b:
        # Both have same name and no params → identical
        return 1.0

    if name_match:
        sim += 0.6

    if vals_a and vals_b:
        matched = 0
        used_b: set = set()
        for va in vals_a:
            best_dist = float("inf")
            best_j = -1
            for j, vb in enumerate(vals_b):
                if j in used_b:
                    continue
                dist = abs(va - vb) / max(abs(va), abs(vb), 1.0)
                if dist < best_dist:
                    best_dist = dist
                    best_j = j
            if best_j >= 0 and best_dist < 0.2:  # within 20% tolerance
                matched += 1
                used_b.add(best_j)

        total = max(len(vals_a), len(vals_b))
        jaccard = matched / total if total > 0 else 0.0
        sim += 0.4 * jaccard

    return sim


# ─── Основной API ──────────────────────────────────────────────────────

def deduplicate_ideas(
    ideas: List[Dict[str, Any]],
    sim_threshold: float = 0.7,
) -> List[Dict[str, Any]]:
    """Дедупликация идей.

    Алгоритм:
      1. Отбросить VETO-идеи (score = -inf)
      2. Группировать по (ticker, direction)
      3. Внутри каждой группы: попарное сравнение sim_threshold
      4. Оставить лучший по score из «близнецов»

    Возвращает список идей без дублей, отсортированный по score (descending).
    """
    if not ideas:
        return []

    # Step 1: filter out VETO
    clean = [idea for idea in ideas if not _is_veto(idea)]

    if not clean:
        return []

    # Step 2: group by (ticker, direction)
    groups: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]] = {}
    for idea in clean:
        key = (idea.get("ticker", ""), idea.get("direction"))
        groups.setdefault(key, []).append(idea)

    # Step 3+4: deduplicate within each group
    result: List[Dict[str, Any]] = []

    for _key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        # Sort by score descending — best first
        sorted_group = sorted(
            group,
            key=lambda x: x.get("score", 0.0) if not math.isinf(x.get("score", 0.0)) else -1e9,
            reverse=True,
        )

        kept: List[Dict[str, Any]] = []
        for candidate in sorted_group:
            is_dup = False
            for existing in kept:
                if _param_similarity(candidate, existing) >= sim_threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(candidate)

        result.extend(kept)

    # Final sort by score descending
    result.sort(
        key=lambda x: x.get("score", 0.0) if not math.isinf(x.get("score", 0.0)) else -1e9,
        reverse=True,
    )
    return result

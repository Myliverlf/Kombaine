"""Regime Allocator — gated-обёртки отбора pool/live.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции — чистые: dict-in → dict-out.

Обёртки:
  - select_live_slots_gated(candidates, cfg, snapshot, gate_cfg):
      если gate_cfg["enabled"] → префильтр через regime_gate.admit →
      делегирование оригинальному select_live_slots.
      enabled=False → байт-в-байт делегирование.

  - filter_pool_candidates(waitlist, snapshot, gate_cfg):
      admit для допуска в signal_pool (аспект «pool» из task.md).

Инварианты плацдарма (candidate_allocator.select_live_slots) сохранены:
  - excluded VETO, top-k ≤ max_slots, contracts ≤ 1.
  - ничего не мутирует state/.
"""
from typing import Any, Dict, List, Optional

from regime_gate import admit


def select_live_slots_gated(
    candidates: List[Dict[str, Any]],
    cfg: dict,
    regime_snapshot: Optional[Dict[str, Any]] = None,
    gate_cfg: Optional[Dict[str, Any]] = None,
    vol_pctl_map: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Gated-обёртка select_live_slots из candidate_allocator.

    candidates: список кандидатов (формат candidate_allocator.select_live_slots).
    cfg: конфиг для allocator (excluded, risk.max_slots, ...).
    regime_snapshot: state/regime_snapshot.json или None.
    gate_cfg: {"enabled": bool} — дефолт enabled=False.
    vol_pctl_map: {ticker: float (0..100)} — перцентили atr_pct по истории.
        Если не передан — все тикеры считаются normal vol.

    Если gate disabled → прямое делегирование без фильтрации.
    Если gate enabled → admit() для каждого кандидата, затем делегирование
    оставшихся в select_live_slots (сохраняя все его инварианты).
    """
    if gate_cfg is None:
        gate_cfg = {}
    if vol_pctl_map is None:
        vol_pctl_map = {}

    gate_enabled = gate_cfg.get("enabled", False)

    if not gate_enabled:
        # Байт-в-байт делегирование — плацдарм не тронут
        from candidate_allocator import select_live_slots
        return select_live_slots(candidates, cfg, regime_snapshot)

    # Gate enabled: префильтр через regime_gate.admit
    gate_cfg_full = cfg  # cfg уже содержит excluded, max_contracts_per_entry и т.д.

    admitted = []
    for c in candidates:
        vol_pctl = vol_pctl_map.get(c.get("ticker", ""), 50.0)
        result = admit(c, regime_snapshot, gate_cfg_full, vol_pctl)
        if result["admit"]:
            # Если гейт ограничил контракты — обновляем кандидата
            if result["cap_contracts"] < c.get("contracts_requested", 1):
                c = dict(c)
                c["contracts_requested"] = result["cap_contracts"]
            admitted.append(c)

    # Делегирование в оригинальный allocator (все инварианты там сохраняются)
    from candidate_allocator import select_live_slots
    return select_live_slots(admitted, cfg, regime_snapshot)


def filter_pool_candidates(
    waitlist: List[Dict[str, Any]],
    regime_snapshot: Optional[Dict[str, Any]],
    gate_cfg: Optional[Dict[str, Any]] = None,
    vol_pctl_map: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Фильтрация кандидатов для допуска в signal_pool через regime gate.

    waitlist: список кандидатов из waitlist/registry.
    regime_snapshot: state/regime_snapshot.json или None.
    gate_cfg: {"enabled": bool, "excluded": [...], "max_contracts_per_entry": int}.
    vol_pctl_map: {ticker: float (0..100)} — перцентили atr_pct.

    Возвращает отфильтрованный список (порядок сохранён).
    Если gate disabled → возвращает оригинальный waitlist без изменений.
    """
    if gate_cfg is None:
        gate_cfg = {}
    if vol_pctl_map is None:
        vol_pctl_map = {}

    gate_enabled = gate_cfg.get("enabled", False)

    if not gate_enabled:
        return list(waitlist)

    admitted = []
    for c in waitlist:
        vol_pctl = vol_pctl_map.get(c.get("ticker", ""), 50.0)
        result = admit(c, regime_snapshot, gate_cfg, vol_pctl)
        if result["admit"]:
            admitted.append(c)

    return admitted

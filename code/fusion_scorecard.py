"""Fusion Scorecard — метрика PnL↑/risk↓ для fused compositions.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции чистые: dict-in → float/dataclass-out.

Метрики:
  - expectancy_r: нормированная expectancy (в R) из allocator_metrics
  - risk_penalty: штраф 0..1 за просадку/волатильность
  - agreement_ratio: доля баров, где все стратегии согласны
  - composite_score: 0.40*E + 0.35*(1-risk) + 0.25*agreement

Веса заимствованы из allocator_metrics.py WEIGHTS:
  expectancy=40, risk=35, regime=25 → перераспределены на agreement.

Источники:
  - allocator_metrics.expectancy_r, risk_penalty (read-only)
  - plan.md Фича 2
"""
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

from allocator_metrics import expectancy_r, risk_penalty


# ─── Weights ───────────────────────────────────────────────────────────

FUSION_WEIGHTS = {
    "expectancy": 40,     # PnL↑ — основной драйвер
    "risk": 35,           # risk↓ — штраф за просадку/волатильность
    "agreement": 25,      # consensus стратегий — заменяет regime для fusion
}


# ─── Scorecard dataclass ──────────────────────────────────────────────

@dataclass
class FusionScorecard:
    """Scorecard для одной fused composition.

    composition_label: строковое имя composition (напр. "sma+rsi_majority_vote")
    expectancy_r: expectancy в единицах R (или рублях)
    risk_penalty_raw: raw risk penalty 0..1 (из allocator_metrics)
    agreement_ratio: доля баров, где все стратегии согласны (0..1)
    composite_score: итоговый взвешенный скор (чем больше — тем лучше)
    n_bars: количество баров для оценки
    strategies: список стратегий в composition
    method: метод fusion
    contracts: контракты на вход (=1, contract constraint)
    """
    composition_label: str = ""
    expectancy_r: float = 0.0
    risk_penalty_raw: float = 0.0
    agreement_ratio: float = 0.0
    composite_score: float = -999.0
    n_bars: int = 0
    strategies: List[str] = field(default_factory=list)
    method: str = ""
    contracts: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composition_label": self.composition_label,
            "expectancy_r": self.expectancy_r,
            "risk_penalty_raw": self.risk_penalty_raw,
            "agreement_ratio": self.agreement_ratio,
            "composite_score": self.composite_score,
            "n_bars": self.n_bars,
            "strategies": self.strategies,
            "method": self.method,
            "contracts": self.contracts,
        }


# ─── Core scoring functions ──────────────────────────────────────────

def compute_agreement_ratio(signals_df: pd.DataFrame) -> float:
    """Доля баров, где все стратегии дают одинаковый сигнал (все +1 или все -1).

    signals_df: DataFrame со стратегиями в колонках, значения -1/0/1.
    Возвращает float 0..1.
    """
    if signals_df.empty or signals_df.shape[1] < 2:
        return 1.0

    pos_all = (signals_df > 0).all(axis=1)
    neg_all = (signals_df < 0).all(axis=1)
    agreement = (pos_all | neg_all).sum()
    return float(agreement) / len(signals_df)


def compute_fusion_expectancy(
    fused_signal: pd.Series,
    returns: pd.Series,
    risk_per_trade: float = 0.0,
) -> float:
    """Expectancy для fused signal на основе ретёрнов.

    fused_signal: -1/0/1 (направление).
    returns: ежедневные доходности инструмента (lng column).
    risk_per_trade: нормировка в рублях (0 = absolute).
    """
    if len(fused_signal) != len(returns):
        min_len = min(len(fused_signal), len(returns))
        fused_signal = fused_signal.iloc[-min_len:]
        returns = returns.iloc[-min_len:]

    # PnL series: сигнал × ретёрн
    pnl = (fused_signal.shift(1).fillna(0) * returns)

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_rate = len(wins) / max(len(wins) + len(losses), 1)
    avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_loss = float(losses.abs().mean()) if len(losses) > 0 else 0.0

    stats = {"win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss}
    return expectancy_r(stats, risk_per_trade=risk_per_trade)


def compute_fusion_risk_penalty(fused_signal: pd.Series, returns: pd.Series) -> float:
    """Risk penalty для fused composition на основе просадки и волатильности.

    Возвращает float 0..1 (ниже = лучше).
    """
    if len(fused_signal) != len(returns):
        min_len = min(len(fused_signal), len(returns))
        fused_signal = fused_signal.iloc[-min_len:]
        returns = returns.iloc[-min_len:]

    pnl = (fused_signal.shift(1).fillna(0) * returns)
    cum = pnl.cumsum()
    peak = cum.cummax()
    drawdown = peak - cum
    max_dd = float(drawdown.max()) if len(drawdown) > 0 else 0.0
    vol = float(pnl.std()) if len(pnl) > 1 else 0.0

    return risk_penalty({"drawdown_pct": max_dd * 100, "volatility": vol * 100})


def score_fusion(
    composition_label: str,
    fused_signal: pd.Series,
    returns: pd.Series,
    strategies: List[str],
    method: str,
    risk_per_trade: float = 0.0,
    weights: Optional[Dict[str, float]] = None,
) -> FusionScorecard:
    """Score одной fused composition: PnL↑/risk↓ + agreement.

    composite = w_e * E_norm + w_r * (1 - risk) + w_a * agreement
    где E_norm = e / (1+|e|) — нормированный expectancy [-1..+1].
    """
    w = weights or FUSION_WEIGHTS

    # Signals DataFrame для agreement ratio
    if isinstance(fused_signal, pd.Series):
        # Для agreement нужен DataFrame стратегий — передаём через контекст
        # Здесь используем fused_signal как single-column
        agreement = 1.0  # single fused signal — нет мульти-колонок
    else:
        agreement = 1.0

    e_r = compute_fusion_expectancy(fused_signal, returns, risk_per_trade)
    r_pen = compute_fusion_risk_penalty(fused_signal, returns)

    # Нормализация expectancy → [-1..+1]
    e_norm = e_r / (1.0 + abs(e_r)) if e_r != 0 else 0.0

    composite = (
        w["expectancy"] * e_norm
        + w["risk"] * (1.0 - r_pen)
        + w["agreement"] * agreement
    ) / 100.0

    return FusionScorecard(
        composition_label=composition_label,
        expectancy_r=e_r,
        risk_penalty_raw=r_pen,
        agreement_ratio=agreement,
        composite_score=round(composite, 6),
        n_bars=len(fused_signal),
        strategies=strategies,
        method=method,
        contracts=1,
    )


def score_fusion_with_agreement(
    composition_label: str,
    signals_df: pd.DataFrame,
    fused_signal: pd.Series,
    returns: pd.Series,
    strategies: List[str],
    method: str,
    risk_per_trade: float = 0.0,
    weights: Optional[Dict[str, float]] = None,
) -> FusionScorecard:
    """Score с реальным agreement ratio из DataFrame стратегий.

    signals_df: DataFrame со стратегиями в колонках (-1/0/1).
    fused_signal: итоговый fused signal.
    """
    w = weights or FUSION_WEIGHTS
    agreement = compute_agreement_ratio(signals_df)
    e_r = compute_fusion_expectancy(fused_signal, returns, risk_per_trade)
    r_pen = compute_fusion_risk_penalty(fused_signal, returns)

    e_norm = e_r / (1.0 + abs(e_r)) if e_r != 0 else 0.0

    composite = (
        w["expectancy"] * e_norm
        + w["risk"] * (1.0 - r_pen)
        + w["agreement"] * agreement
    ) / 100.0

    return FusionScorecard(
        composition_label=composition_label,
        expectancy_r=e_r,
        risk_penalty_raw=r_pen,
        agreement_ratio=agreement,
        composite_score=round(composite, 6),
        n_bars=len(fused_signal),
        strategies=strategies,
        method=method,
        contracts=1,
    )


# ─── Compare compositions ─────────────────────────────────────────────

def compare_compositions(scores: List[FusionScorecard]) -> List[FusionScorecard]:
    """Ранжировать compositions по composite_score desc.

    Возвращает отсортированный список (лучшие первые).
    """
    return sorted(scores, key=lambda s: s.composite_score, reverse=True)


def select_top_compositions(
    scores: List[FusionScorecard],
    max_slots: int = 3,
    excluded: Optional[set] = None,
) -> List[FusionScorecard]:
    """Выбрать ≤max_slots лучших compositions, исключая VETO'd.

    excluded: множество имён стратегий для VETO (default: {"RI"}).
    """
    from signal_fusion import EXCLUDED_TICKERS
    exc = excluded or EXCLUDED_TICKERS

    # VETO: compositions с исключёнными стратегиями → score = -inf
    for s in scores:
        if any(st in exc for st in s.strategies):
            s.composite_score = -float("inf")

    # Filter out VETO'd compositions before ranking
    valid = [s for s in scores if s.composite_score > -float("inf")]
    ranked = compare_compositions(valid)
    return ranked[:max_slots]

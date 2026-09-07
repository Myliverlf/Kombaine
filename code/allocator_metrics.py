"""Allocator Metrics — чистые метрики кандидата для ранжирования.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции — чистые: dict-in → float-out.

Метрики:
  - expectancy_r: нормированная expectancy (в R)
  - risk_penalty: штраф 0..1 за просадку/волатильность слота
  - regime_bonus: бонус/штраф за совпадение с regime-направлением

Источники:
  - expectancy формула: win_rate * avg_win - (1 - win_rate) * avg_loss,
    нормировано на risk_per_trade (research.md F1/F3).
  - risk_penalty: переиспользует логику calc_volatility/calc_drawdown
    из risk_scorecard (read-only, не копирует — принимает уже посчитанные значения).
  - regime_bonus: regime_snapshot.tickers[ticker].{direction, regime}.

Связь с плацдармом:
  - Существующий rank_score (seeder.py:74-85) = pnl + sharpe*1000 + ... —
    НЕ содержит expectancy/risk/regime. Этот модуль закрывает гэп.
"""
from typing import Any, Dict, Optional


# ─── Веса allocator-скоринга ────────────────────────────────────────────
# Аналог _WEIGHTS в risk_scorecard.py:320
WEIGHTS = {
    "expectancy": 40,   # основной драйвер — PnL↑
    "risk": 35,         # штраф за риск — risk↓
    "regime": 25,       # бонус за совпадение с regime
}


# ─── 1. Expectancy (в R) ───────────────────────────────────────────────

def expectancy_r(stats: Dict[str, Any], risk_per_trade: float = 0.0) -> float:
    """Нормированная expectancy кандидата в единицах R.

    Формула:
        raw_e = win_rate * avg_win - (1 - win_rate) * avg_loss
        E[R] = raw_e / risk_per_trade   (если risk_per_trade > 0)
              иначе raw_e                (absolute rub-expectancy)

    stats: {win_rate, avg_win, avg_loss} — все >= 0.
        win_rate  — доля выигрышных сделок (0..1)
        avg_win   — средний выигрыш в рублях (>0)
        avg_loss  — средний проигрыш в рублях (>=0)

    Возвращает float: expectancy в R (или рублях).

    Пример:
        >>> expectancy_r({"win_rate": 0.5, "avg_win": 2.0, "avg_loss": 1.0}, 1.0)
        0.5
        >>> expectancy_r({"win_rate": 0.6, "avg_win": 200.0, "avg_loss": 100.0})
        20.0
    """
    win_rate = float(stats.get("win_rate", 0.0))
    avg_win = float(stats.get("avg_win", 0.0))
    avg_loss = float(stats.get("avg_loss", 0.0))

    raw_e = win_rate * avg_win - (1.0 - win_rate) * avg_loss

    if risk_per_trade > 0:
        return raw_e / risk_per_trade
    return raw_e


# ─── 2. Risk penalty ───────────────────────────────────────────────────

def risk_penalty(slot: Dict[str, Any]) -> float:
    """Штраф за риск слота, нормирован 0..1 (ниже = лучше).

    Два источника:
      A) slot.drawdown_pct — текущая просадка слота в % (0..100+).
         Штраф = min(drawdown_pct / 15, 1.0). 15% — аналог slot_eject_slot_drawdown_pct.
      B) slot.volatility — нормированная волатильность (std/atr, как calc_volatility).
         Шторма > 5 → WARN, > 15 → VETO в scorecard.

    Итог = max(penalty_drawdown, penalty_volatility) — худшая компонента.

    Если данных нет — штраф = 0.3 (консервативная середина).

    Пример:
        >>> risk_penalty({"drawdown_pct": 7.5})  # 7.5/15 = 0.5
        0.5
        >>> risk_penalty({})  # нет данных → 0.3
        0.3
    """
    dd_pct = slot.get("drawdown_pct")
    vol = slot.get("volatility")

    penalties = []

    if dd_pct is not None:
        # штраф линейно от 0% (без просадки) до 15% (max eject threshold)
        penalty_dd = min(abs(float(dd_pct)) / 15.0, 1.0)
        penalties.append(penalty_dd)

    if vol is not None:
        # нормированная vol > 5 → шторма, > 15 → VETO
        penalty_vol = min(abs(float(vol)) / 15.0, 1.0)
        penalties.append(penalty_vol)

    if not penalties:
        # нет данных — консервативная середина
        return 0.3

    return max(penalties)


# ─── 3. Regime bonus ───────────────────────────────────────────────────

def regime_bonus(
    ticker: str,
    direction: Optional[str],
    regime_snapshot: Dict[str, Any],
) -> float:
    """Бонус/штраф за совпадение кандидата с regime-направлением.

    Результат -1.0 .. +1.0:
      +1.0  — кандидат long (direction=LONG), regime=trend, bias=up (или наоборот short/down)
      +0.5  — кандидат long, regime=trend, bias=neutral (сильный тренд, но нет контекста)
      +0.0  — regime=range (нейтрально — тренд-фильтр неактивен)
      -0.5  — кандидат long, regime=trend, direction=down (против тренда)
      -1.0  — кандидат long, regime=trend, direction=down, adx > 30 (сильный тренд против)

    ticker: тикер кандидата (напр. "LKOH")
    direction: "LONG" / "SHORT" / None
    regime_snapshot: формат state/regime_snapshot.json
        {"tickers": {"T": {"adx": ..., "direction": "up"/"down", "regime": "trend"/"range"}},
         "bias": "neutral"/"up"/"down"}

    Пример:
        >>> regime_bonus("BR", "LONG", {"tickers": {"BR": {"adx": 25, "direction": "up", "regime": "trend"}}, "bias": "neutral"})
        0.5
    """
    if direction is None:
        return 0.0

    tickers = regime_snapshot.get("tickers", {})
    ticker_data = tickers.get(ticker, {})

    regime = ticker_data.get("regime", "range")
    adx = float(ticker_data.get("adx", 0.0))
    reg_direction = ticker_data.get("direction", "neutral")

    # Определяем направление кандидата в терминах up/down
    cand_dir = "up" if direction.upper() == "LONG" else "down"

    if regime == "range":
        # В рейнже тренд-фильтр неактивен — нейтрально
        return 0.0

    # regime == "trend"
    if reg_direction == cand_dir:
        # Идём в направлении тренда — бонус
        # Чем выше ADX, тем сильнее бонус (макс при ADX >= 40)
        bonus = 0.5 + 0.5 * min(adx / 40.0, 1.0)
        return round(bonus, 3)
    else:
        # Идём против тренда — штраф
        penalty = -(0.5 + 0.5 * min(adx / 40.0, 1.0))
        return round(penalty, 3)


# ─── Composite score ────────────────────────────────────────────────────

def allocator_score(
    candidate: Dict[str, Any],
    regime_snapshot: Dict[str, Any],
    risk_per_trade: float = 0.0,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Композитный allocator-score для кандидата.

    candidate: {
        "ticker": "LKOH",
        "direction": "LONG",        # "LONG"/"SHORT"/None
        "win_rate": 0.55,
        "avg_win": 150.0,
        "avg_loss": 80.0,
        "drawdown_pct": 5.0,        # опционально
        "volatility": 2.0,          # опционально
    }

    regime_snapshot: state/regime_snapshot.json

    risk_per_trade: размер типичного риска в рублях (0 = без нормировки)

    weights: override WEIGHTS

    Возвращает float: чем больше — тем лучше кандидат.
    """
    w = weights or WEIGHTS

    # Expectancy R
    e_r = expectancy_r(
        {"win_rate": candidate.get("win_rate", 0.0),
         "avg_win": candidate.get("avg_win", 0.0),
         "avg_loss": candidate.get("avg_loss", 0.0)},
        risk_per_trade=risk_per_trade,
    )

    # Risk penalty (0..1, ниже лучше → инвертируем для score)
    r_pen = risk_penalty(candidate)

    # Regime bonus (-1..+1)
    reg = regime_bonus(
        candidate.get("ticker", ""),
        candidate.get("direction"),
        regime_snapshot,
    )

    # Weighted sum: expectancy + (1 - risk) + regime
    # Нормируем expectancy к ~[0..1] через sigmoid-подобную функцию:
    # e_norm = e_r / (1 + abs(e_r)) → [-1..+1]
    e_norm = e_r / (1.0 + abs(e_r)) if e_r != 0 else 0.0

    score = (
        w["expectancy"] * e_norm
        + w["risk"] * (1.0 - r_pen)
        + w["regime"] * reg
    ) / 100.0

    return round(score, 6)

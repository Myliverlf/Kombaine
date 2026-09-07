"""Strategy Ideas — Pre-backtest quality scoring и template matcher.

Модуль закрывает пробел pipeline: нет оценки стратегий-идей ДО expensive бэктеста (15м).
Все функции — чистые: dict-in → dict/float-out.

Функции:
  - idea_score(idea, regime_snapshot, feedback, config) → float
    Взвешенный скор: expectancy 40%, risk 30%, regime 20%, feedback 15%.
    VETO для excluded тикеров (RI). contracts=1. return -inf для VETO'd.
  - filter_weak(ideas, threshold=0.3) → list
    Отсекает ideas с score < threshold.
  - suggest_templates(regime_snapshot, direction_hint=None) → list[str]
    Regime→template mapping: trend→[ema_cross, donchian, ft_supertrend],
    range→[mean_reversion, bollinger_squeeze], default→all.
  - build_idea(ticker, strategy_name, params_hint=None) → dict
    Конструктор идеи {ticker, strategy_name, params, contracts:1, score:None}.

Зависимости: stdlib + allocator_metrics (expectancy_r, risk_penalty, regime_bonus).
Нет broker/tinkoff/futures_lab импортов.
"""
import math
import re
from typing import Any, Dict, List, Optional

from allocator_metrics import expectancy_r, risk_penalty, regime_bonus


# ─── Веса pre-backtest скоринга ───────────────────────────────────────
# Аналог WEIGHTS в allocator_metrics, но с.feedback-компонентой
IDEA_WEIGHTS = {
    "expectancy": 40,  # PnL↑
    "risk": 30,        # risk↓
    "regime": 20,      # совпадение с regime
    "feedback": 15,    # на основе historical feedback (лучшие часы и т.д.)
}

# Regime → template mapping (из STRATEGY_FUNCS/futures_lab, but names only)
REGIME_TEMPLATE_MAP = {
    "trend": ["ema_cross", "donchian", "ft_supertrend"],
    "range": ["mean_reversion", "bollinger_squeeze"],
}

# Все известные шаблоны (44 из STRATEGY_FUNCS, подмножество — safe defaults)
ALL_TEMPLATES = [
    "ema_cross", "donchian", "ft_supertrend",
    "mean_reversion", "bollinger_squeeze",
    "rsi_reversal", "macd_divergence", "adx_breakout",
    "supertrend_adx", "crossover_momentum",
]

# Захардкоженные стратегии-индикаторы (паттерн-матчинг для templates)
TREND_TEMPLATES = {"ema_cross", "donchian", "ft_supertrend", "adx_breakout",
                   "supertrend_adx", "crossover_momentum"}
RANGE_TEMPLATES = {"mean_reversion", "bollinger_squeeze", "rsi_reversal",
                   "macd_divergence"}

# ─── Feedback helpers ──────────────────────────────────────────────────

def _extract_feedback_score(feedback: Dict[str, Any], direction: Optional[str]) -> float:
    """Извлечь нормированный feedback-score из historical feedback.

    Возвращает -1..+1:
      +1 — feedback показывает что этот direction хороший (positive PnL)
      -1 — feedback показывает что этот direction плохой
       0 — нет данных
    """
    if not feedback or direction is None:
        return 0.0

    direction = direction.upper()
    # feedback формат: {"LONG": {"pnl": float}, "SHORT": {"pnl": float}, ...}
    dir_data = feedback.get(direction, {})
    pnl = dir_data.get("pnl", 0.0)

    # Нормируем: PnL > 200 → +1, < -200 → -1
    if pnl > 0:
        return min(pnl / 200.0, 1.0)
    else:
        return max(pnl / 200.0, -1.0)


def _extract_best_hours_bonus(feedback: Dict[str, Any]) -> float:
    """Бонус за best_hours из feedback (напр. [8, 7] — утренние часы).

    Возвращает 0..+0.2 — небольшой бонус если feedback содержит best_hours.
    """
    if not feedback:
        return 0.0
    best_hours = feedback.get("best_hours", [])
    if not best_hours:
        return 0.0
    # Чем больше best_hours, тем больше бонус (макс 5 часов → +0.2)
    return min(len(best_hours) * 0.04, 0.2)


# ─── Core: idea_score ─────────────────────────────────────────────────

def idea_score(
    idea: Dict[str, Any],
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    """Pre-backtest quality score для стратегии-идеи.

    idea: {
        "ticker": str,
        "strategy_name": str,
        "direction": "LONG" | "SHORT" | None,
        "win_rate": float (optional, expected 0..1),
        "avg_win": float (optional),
        "avg_loss": float (optional),
        "drawdown_pct": float (optional),
        "volatility": float (optional),
    }

    regime_snapshot: state/regime_snapshot.json
    feedback: state/generator_feedback.json
    config: {excluded: [...], max_contracts_per_entry: int, ...}

    Возвращает float: score (чем больше — тем лучше) или -inf для VETO.
    """
    if config is None:
        config = {}

    ticker = idea.get("ticker", "")
    direction = idea.get("direction")

    # ─── VETO: excluded tickers ────────────────────────────────────────
    excluded = config.get("excluded", [])
    if ticker in excluded:
        return float("-inf")

    # ─── Expectancy component ──────────────────────────────────────────
    candidate = {
        "win_rate": idea.get("win_rate", 0.5),
        "avg_win": idea.get("avg_win", 100.0),
        "avg_loss": idea.get("avg_loss", 100.0),
    }
    e_r = expectancy_r(candidate, risk_per_trade=0.0)
    # Нормируем к 0..1 через sigmoid
    e_norm = (e_r / (1.0 + abs(e_r))) if e_r != 0 else 0.0

    # ─── Risk component ────────────────────────────────────────────────
    slot_data = {
        "drawdown_pct": idea.get("drawdown_pct"),
        "volatility": idea.get("volatility"),
    }
    r_pen = risk_penalty(slot_data)  # 0..1, ниже лучше
    # Инвертируем: 1.0 - penalty → Score component

    # ─── Regime component ──────────────────────────────────────────────
    if regime_snapshot is not None:
        reg = regime_bonus(ticker, direction, regime_snapshot)  # -1..+1
    else:
        reg = 0.0

    # ─── Feedback component ────────────────────────────────────────────
    fb_score = _extract_feedback_score(feedback or {}, direction)
    fb_hours_bonus = _extract_best_hours_bonus(feedback or {})
    fb_total = fb_score + fb_hours_bonus

    # ─── Composite weighted score ──────────────────────────────────────
    w = IDEA_WEIGHTS
    score = (
        w["expectancy"] * e_norm
        + w["risk"] * (1.0 - r_pen)
        + w["regime"] * reg
        + w["feedback"] * fb_total
    ) / 100.0

    return round(score, 6)


# ─── filter_weak ───────────────────────────────────────────────────────

def filter_weak(ideas: List[Dict[str, Any]], threshold: float = 0.3) -> List[Dict[str, Any]]:
    """Отсечь слабые идеи: score < threshold.

    Предполагается что у каждой idea уже есть поле "score".
    Если score отсутствует — считаем что idea не прошла scoring (score=None → фильтруем).
    """
    result = []
    for idea in ideas:
        score = idea.get("score")
        if score is None:
            continue
        if math.isinf(score) and score < 0:
            # VETO'd ideas — исключаем
            continue
        if score >= threshold:
            result.append(idea)
    return result


# ─── suggest_templates ─────────────────────────────────────────────────

def suggest_templates(
    regime_snapshot: Optional[Dict[str, Any]] = None,
    direction_hint: Optional[str] = None,
) -> List[str]:
    """Предложить шаблоны стратегий на основе regime + direction.

    Regime→template mapping:
      trend → [ema_cross, donchian, ft_supertrend]
      range → [mean_reversion, bollinger_squeeze]
      None/другой → все известные шаблоны (fallback)

    direction_hint: "LONG"/"SHORT"/None — может сузить выбор
      (например, в range+LONG — mean_reversion предпочтительнее).
    """
    if regime_snapshot is None:
        return list(ALL_TEMPLATES)

    # Смотрим на общий bias и regime тикеров
    tickers = regime_snapshot.get("tickers", {})
    bias = regime_snapshot.get("bias", "neutral")

    # Считаем количество regime по тикерам
    trend_count = 0
    range_count = 0
    for _ticker, data in tickers.items():
        regime = data.get("regime", "range")
        if regime == "trend":
            trend_count += 1
        elif regime == "range":
            range_count += 1

    # Доминирующий regime
    if trend_count > range_count:
        dominant = "trend"
    elif range_count > trend_count:
        dominant = "range"
    else:
        dominant = "mixed"

    templates = []

    if dominant in REGIME_TEMPLATE_MAP:
        templates.extend(REGIME_TEMPLATE_MAP[dominant])

    if dominant == "mixed":
        # Берём подмножество из обоих
        templates.extend(REGIME_TEMPLATE_MAP["trend"][:2])
        templates.extend(REGIME_TEMPLATE_MAP["range"][:2])

    # Fallback: если templates пусты
    if not templates:
        templates = list(ALL_TEMPLATES)

    return templates


# ─── build_idea ────────────────────────────────────────────────────────

def build_idea(
    ticker: str,
    strategy_name: str,
    params_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Конструктор идеи для pre-backtest scoring.

    Возвращает dict:
    {
        "ticker": str,
        "strategy_name": str,
        "params": dict | None,
        "contracts": 1,
        "score": None,
        "direction": None,
    }

    contracts ВСЕГДА = 1 (максимальный контракт на вход — config constraint).
    """
    return {
        "ticker": ticker,
        "strategy_name": strategy_name,
        "params": params_hint,
        "contracts": 1,
        "score": None,
        "direction": None,
    }


# ─── score_ideas_batch ─────────────────────────────────────────────────

def score_ideas_batch(
    ideas: List[Dict[str, Any]],
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Batch scoring: оценить список идей и записать score обратно.

    Возвращает отсортированный по score (descending) список.
    VETO'd ideas (score=-inf) остаются в списке, но в конце.
    """
    scored = []
    for idea in ideas:
        s = idea_score(idea, regime_snapshot, feedback, config)
        scored_idea = dict(idea)
        scored_idea["score"] = s
        scored_idea["contracts"] = 1  # ВСЕГДА 1 (config constraint)
        scored.append(scored_idea)

    # Сортировка: -inf в конце, остальные по убыванию
    scored.sort(key=lambda x: (math.isinf(x["score"]) and x["score"] < 0, -x["score"]))
    return scored


# ─── AST-guard: no broker calls ────────────────────────────────────────

def check_no_broker_imports() -> bool:
    """Проверить что модуль не импортирует broker/client/tinkoff/futures_lab.

    Возвращает True если чисто (нет forbidden импортов).
    """
    import ast as _ast
    from pathlib import Path as _Path

    # Получаем путь к этому модулю
    module_path = _Path(__file__).resolve()
    source = module_path.read_text()
    tree = _ast.parse(source)

    forbidden_names = {"Client", "post_order", "place_order", "send_order",
                       "submit_order", "futures_lab", "tinkoff"}
    forbidden_imports = {"tinkoff", "futures_lab", "broker"}

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in forbidden_imports:
                    return False
        elif isinstance(node, _ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in forbidden_imports:
                    return False
        elif isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id in forbidden_names:
                return False
            if isinstance(func, _ast.Attribute) and func.attr in forbidden_names:
                return False

    return True

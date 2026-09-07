"""Regime Gate — классификатор regime + гейт допуска стратегий в pool/live.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции — чистые: dict-in → dict-out.

Классификатор:
  - classify_ticker({adx, atr_pct}, vol_pctl) → regime ∈ {trend, chop, transitional}
    + vol_bucket ∈ {low, normal, high}
  - Пороги ADX: ≥25 → trend (Уайлдер), <20 → chop, 20–25 → transitional (pass-through)
  - Vol: atr_pct перцентиль ≥80 → high, ≤20 → low, иначе normal

Гейт допуска:
  - admit(candidate, snapshot, cfg) → {admit, reason, cap_contracts}
  - Порядок проверок: excluded(RI) VETO → stale/missing snapshot → pass-through →
    chop+directional → deny; vol=high → cap_contracts=1; trend/match → admit

Пороги захардкожены как именованные константы с комментариями-источниками.
Нет записи в state/.
"""
from typing import Any, Dict, Optional


# ─── Пороги классификатора ─────────────────────────────────────────────

# ADX thresholds (source: Уайлдер / StockCharts / research.md facts 1-4)
ADX_TREND_MIN = 25.0    # ADX >= 25 → тренд
ADX_CHOP_MAX = 20.0     # ADX < 20 → chop

# ATR% percentile thresholds for vol bucket
VOL_HIGH_PCTL = 80.0    # перцентиль >= 80 → high
VOL_LOW_PCTL = 20.0     # перцентиль <= 20 → low

# Snapshot freshness (source: core/supervisor.py:66 regime_fresh)
SNAPSHOT_MAX_AGE_S = 6 * 3600  # 6 hours


# ─── Классификатор тикера ──────────────────────────────────────────────

def classify_ticker(
    ticker_data: Dict[str, Any],
    vol_pctl: float = 50.0,
) -> Dict[str, Any]:
    """Классифицировать regime и vol_bucket для одного тикера.

    ticker_data: {"adx": float, "atr_pct": float, "direction": str, ...}
        — элемент snapshot.tickers[ticker]

    vol_pctl: текущий перцентиль atr_pct тикера в истории (0..100).
        Если история недоступна — дефолт 50.

    Возвращает: {
        "regime": "trend" | "chop" | "transitional",
        "vol_bucket": "low" | "normal" | "high",
        "adx": float,
        "atr_pct": float,
        "direction": str
    }
    """
    adx = float(ticker_data.get("adx", 0.0))
    atr_pct = float(ticker_data.get("atr_pct", 0.0))
    direction = ticker_data.get("direction", "neutral")

    # Regime classification
    if adx >= ADX_TREND_MIN:
        regime = "trend"
    elif adx < ADX_CHOP_MAX:
        regime = "chop"
    else:
        regime = "transitional"

    # Vol bucket classification
    if vol_pctl >= VOL_HIGH_PCTL:
        vol_bucket = "high"
    elif vol_pctl <= VOL_LOW_PCTL:
        vol_bucket = "low"
    else:
        vol_bucket = "normal"

    return {
        "regime": regime,
        "vol_bucket": vol_bucket,
        "adx": adx,
        "atr_pct": atr_pct,
        "direction": direction,
    }


# ─── Гейт допуска ──────────────────────────────────────────────────────

def admit(
    candidate: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    vol_pctl: float = 50.0,
) -> Dict[str, Any]:
    """Решение о допуске кандидата в pool/live.

    candidate: {
        "ticker": str,
        "direction": "LONG" / "SHORT" / None,
        "contracts_requested": int (optional, default 1),
    }

    snapshot: state/regime_snapshot.json или None.
        Формат: {"ts": "...", "tickers": {"T": {"adx", "direction", "regime", "atr_pct"}},
                 "bias": str}

    cfg: {"excluded": [...], "max_contracts_per_entry": int} или None (= defaults)

    vol_pctl: перцентиль atr_pct для тикера (0..100). Если snapshot содержит
        данные тикера — перцентиль можно вычислить вызывающей стороной.

    Возвращает: {
        "admit": bool,
        "reason": str,
        "cap_contracts": int,
        "regime_info": dict | None
    }
    """
    if cfg is None:
        cfg = {}

    excluded = cfg.get("excluded", [])
    max_contracts = cfg.get("max_contracts_per_entry", 1)

    ticker = candidate.get("ticker", "")
    direction = candidate.get("direction")
    contracts_requested = candidate.get("contracts_requested", 1)
    contracts = min(contracts_requested, max_contracts)

    # 1. VETO: excluded tickers (first check, as in risk.py:103 and candidate_allocator.py)
    if ticker in excluded:
        return {
            "admit": False,
            "reason": "excluded",
            "cap_contracts": 0,
            "regime_info": None,
        }

    # 2. Missing/stale snapshot → pass-through (preserve current behavior)
    if snapshot is None or not isinstance(snapshot, dict):
        return {
            "admit": True,
            "reason": "no_regime_data",
            "cap_contracts": contracts,
            "regime_info": None,
        }

    tickers = snapshot.get("tickers", {})
    ticker_data = tickers.get(ticker)

    if ticker_data is None:
        return {
            "admit": True,
            "reason": "no_regime_data",
            "cap_contracts": contracts,
            "regime_info": None,
        }

    # Check snapshot freshness
    ts_str = snapshot.get("ts", "")
    if ts_str:
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(ts_str)
            now = datetime.now(timezone.utc)
            age_s = (now - ts).total_seconds()
            if age_s > SNAPSHOT_MAX_AGE_S:
                return {
                    "admit": True,
                    "reason": "stale_snapshot",
                    "cap_contracts": contracts,
                    "regime_info": None,
                }
        except (ValueError, TypeError):
            # Unable to parse ts — treat as no data
            return {
                "admit": True,
                "reason": "no_regime_data",
                "cap_contracts": contracts,
                "regime_info": None,
            }

    # 3. Classify ticker
    info = classify_ticker(ticker_data, vol_pctl)

    # 4. Chop + directional strategy → deny
    if info["regime"] == "chop" and direction is not None:
        return {
            "admit": False,
            "reason": "chop_regime_directional",
            "cap_contracts": 0,
            "regime_info": info,
        }

    # 5. High volatility → cap contracts at 1
    if info["vol_bucket"] == "high":
        return {
            "admit": True,
            "reason": "high_vol_capped",
            "cap_contracts": min(contracts, 1),
            "regime_info": info,
        }

    # 6. Transitional regime with directional strategy → downgrade (cap)
    if info["regime"] == "transitional" and direction is not None:
        return {
            "admit": True,
            "reason": "transitional_capped",
            "cap_contracts": min(contracts, 1),
            "regime_info": info,
        }

    # 7. Default: admit
    return {
        "admit": True,
        "reason": "admitted",
        "cap_contracts": contracts,
        "regime_info": info,
    }

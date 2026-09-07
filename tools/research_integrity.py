#!/usr/bin/env python3
"""tools/research_integrity.py — научная честность отбора (hardening v2, §5).

ТРИ задачи:
1. MULTIPLE-TESTING CORRECTION: CSCV-PBO + Deflated Sharpe Ratio
   (Bailey/Borwein/Lopez de Prado/Zhu 2017; Bailey & Lopez de Prado 2014).
   Обоснование выбора методов — /root/audits/strategy_combine_hardening_v2/07_multiple_testing_report.md
2. DATASET IDENTITY: dataset_id = sha256(содержимое CSV)[:16] — НЕ имя файла.
   Поймал реальный баг: LKOH_1095d побайтово равен LKOH_365d (2690 строк).
3. EXPERIMENT REGISTRY: append-only JSONL — нельзя терять проигравшие эксперименты;
   победитель обязан знать, из скольких попыток он выбран.

Модуль чистый: детерминированная математика + файловые записи. Брокера не зовёт.
Проверен тестами: tests/test_research_integrity.py и аудит-прогоном на реальных данных
(44 стратегии × 365 дней → PBO 0.8056, DSR 0.79–0.83 при trials=14400).
"""
from __future__ import annotations
import hashlib, json, math, os, time
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA_VERSION = "research-integrity-v1"
GAMMA = 0.5772156649015329  # Euler-Mascheroni
REGISTRY_PATH = Path("/root/prop-desk/strategy_combine/state/experiment_registry.jsonl")

# пороги (фиксируются ДО прогона; менять задним числом ради любимой стратегии нельзя)
DSR_MIN = 0.95        # вероятность «истинный SR>0» после поправки на trials
PBO_MAX = 0.50        # доля разбиений, где train-победитель ниже медианы на test


# ───────────────────────── нормальное распределение (без scipy) ─────────────────────────
def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam, |err| < 1.15e-9)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ───────────────────────── метрики ─────────────────────────
def sharpe(returns, periods_per_year: float = 252.0) -> float:
    """Annualized Sharpe. returns = 1-D array-like."""
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    if not np.isfinite(sd) or sd < 1e-12:   # константа/мусор → SR не определён
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def per_period_sharpe(returns) -> float:
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    if not np.isfinite(sd) or sd < 1e-12:
        return 0.0
    return float(r.mean() / sd)


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """E[max SR] под H0 (все стратегии одинаково бесполезны) при n_trials попытках.
    SR* = sr_std·((1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)))   [Bailey & Lopez de Prado]"""
    n = max(2, int(n_trials))
    if sr_std <= 0:
        return 0.0
    t1 = _norm_ppf(1.0 - 1.0 / n)
    t2 = _norm_ppf(1.0 - 1.0 / (n * math.e))
    return float(sr_std * ((1 - GAMMA) * t1 + GAMMA * t2))


def deflated_sharpe_ratio(observed_sr_perperiod: float, n_trials: int, n_obs: int,
                          skew: float, kurtosis: float, sr_std_perperiod: float) -> float:
    """DSR = Φ[(SR_obs − SR*)·√(n−1) / √(1 − skew·SR + (kurt−1)/4·SR²)].
    Все Sharpe — ПЕРИОДИЧЕСКИЕ (не аннуализированные), как в исходной формуле.
    Возвращает P(истинный SR > 0) с поправкой на выбор максимума из n_trials."""
    sr = float(observed_sr_perperiod)
    sr_star = expected_max_sharpe(n_trials, sr_std_perperiod)
    denom_var = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    if denom_var <= 0 or n_obs < 3:
        return 0.0
    z = (sr - sr_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_var)
    return float(_norm_cdf(z))


def pbo_cscv(returns_matrix, S: int = 10, perf: str = "sharpe") -> dict:
    """CSCV → Probability of Backtest Overfitting.

    returns_matrix: (N стратегий × T периодов). T усекается до кратного S.
    Для всех C(S, S/2) разбиений: train = S/2 блоков, test = дополнение.
    Победитель на train → ранг ω=(rank+1)/(N+1) на test. PBO = P(ω < 0.5).
    """
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim != 2 or M.shape[0] < 3:
        return {"pbo": float("nan"), "logit": float("nan"), "error": "need >=3 strategies"}
    N, T = M.shape
    S = int(S)
    if S < 2 or S % 2:
        S = 10
    usable_T = (T // S) * S
    if usable_T < S * 2:
        return {"pbo": float("nan"), "logit": float("nan"), "error": "T too short for S"}
    M = M[:, :usable_T]
    block = usable_T // S
    blocks = [M[:, i * block:(i + 1) * block] for i in range(S)]

    def perf_vec(mat):
        if perf == "sharpe":
            sd = mat.std(axis=1, ddof=1)
            mu = mat.mean(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                v = np.where(sd > 1e-12, mu / np.where(sd > 1e-12, sd, 1.0), 0.0)
            return np.nan_to_num(v)
        return mat.sum(axis=1)

    half = S // 2
    omegas = []
    combos = list(combinations(range(S), half))
    for comb in combos:
        train = np.concatenate([blocks[i] for i in comb], axis=1)
        test_idx = [i for i in range(S) if i not in comb]
        test = np.concatenate([blocks[i] for i in test_idx], axis=1)
        winner = int(np.argmax(perf_vec(train)))
        order = np.argsort(np.argsort(perf_vec(test)))
        omegas.append((int(order[winner]) + 1) / (N + 1))
    omegas = np.array(omegas)
    below = float((omegas < 0.5).mean())
    eps = 1e-6
    lam = min(max(below, eps), 1 - eps)
    return {"pbo": below, "logit": float(math.log(lam / (1 - lam))),
            "n_strategies": int(N), "n_combos": len(combos), "T_used": int(usable_T),
            "below_median_frac": below}


# ───────────────────────── dataset identity (§4) ─────────────────────────
def dataset_id(path) -> str:
    """Content-derived id: ds_<sha256(файл)[:16]>. НЕ зависит от имени файла —
    поэтому LKOH_1095d и LKOH_365d с одинаковым содержимым дают ОДИН id."""
    p = Path(path)
    if not p.exists():
        return "ds_MISSING"
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return "ds_" + h.hexdigest()[:16]


# ───────────────────────── experiment registry (§5) ─────────────────────────
def append_experiment(records) -> Path:
    """Append-only JSONL. Проигравшие НЕ теряются: пишем агрегат по всем попыткам
    (число оценок + распределение fitness) и по каждому финалисту."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    fd = os.open(str(REGISTRY_PATH), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            for r in (records if isinstance(records, list) else [records]):
                rec = {"schema_version": SCHEMA_VERSION, "ts": ts, **r}
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return REGISTRY_PATH


def score_candidate(daily_returns, trials_count: int, sr_std_perperiod: float) -> dict:
    """raw_score / selection_adjusted_score / trials_count / overfit_risk для ОДНОГО
    кандидата (§5). daily_returns = 1-D массив дневных доходностей."""
    r = np.asarray(daily_returns, dtype=float)
    raw = per_period_sharpe(r)
    s = pd.Series(r)
    skew = float(s.skew()) if len(r) > 3 and pd.notna(s.skew()) else 0.0
    kurt = float(s.kurt() + 3.0) if len(r) > 4 and pd.notna(s.kurt()) else 3.0
    dsr = deflated_sharpe_ratio(raw, n_trials=max(1, int(trials_count)), n_obs=len(r),
                               skew=skew, kurtosis=kurt,
                               sr_std_perperiod=max(sr_std_perperiod, 1e-6))
    tc = max(1, int(trials_count))
    return {
        "raw_score": round(raw, 6),
        "selection_adjusted_score": round(dsr, 4),
        "trials_count": tc,
        "overfit_risk": round(1.0 - dsr, 4),          # чем ниже DSR, тем выше риск
        "n_obs": int(len(r)),
        "sr_star_under_null": round(expected_max_sharpe(tc, max(sr_std_perperiod, 1e-6)), 6),
    }

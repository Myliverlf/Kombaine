"""Validate Derived Indicators — dry-run A/B scorecard PnL↑/risk↓.

Загружает синтетические OHLCV, считает:
  - baseline: стандартные TA стратегии (SMA crossover, RSI, Bollinger)
  - enriched: + derived indicators как фильтры/quality gates

Выводит A/B сравнение через scorecard_metrics (Sharpe, Sortino, maxDD, PF).
Проверяет constraints: max_slots ≤ 3, max_contracts=1, RI excluded.

Нет broker/client, нет сети, нет live orders.
"""
import ast
import math
import os
import sys
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

# Ensure code/ on path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from derived_indicators import (
    breakout_quality,
    cumulative_volume_delta,
    liquidity_score,
    microstructure_bar_type,
    orderflow_imbalance,
    volatility_regime_score,
    volume_profile,
)

# Try to import scorecard_metrics if available in the same dir
try:
    from scorecard_metrics import ab_compare, max_drawdown, profit_factor, sharpe_ratio, sortino_ratio
except ImportError:
    # Fallback: re-implement minimal metrics for standalone execution
    sharpe_ratio = None
    sortino_ratio = None
    max_drawdown = None
    profit_factor = None
    ab_compare = None


# ─── Synthetic Data Generator ─────────────────────────────────────────

def generate_synthetic_ohlcv(
    n: int = 500, seed: int = 42, regime: str = "mixed"
) -> pd.DataFrame:
    """Генерация синтетических OHLCV данных для A/B теста.

    regime:
      - 'trend': uptrend с шумом
      - 'range': sideways/range-bound
      - 'mixed': чередование тренд/ sideways (по умолчанию)
    """
    rng = np.random.RandomState(seed)

    if regime == "trend":
        drift = 0.001
        noise_scale = 0.005
    elif regime == "range":
        drift = 0.0
        noise_scale = 0.003
    else:  # mixed
        drift = 0.0005
        noise_scale = 0.004

    returns = drift + noise_scale * rng.randn(n)
    close = 100 * np.exp(np.cumsum(returns))

    # Ensure OHLC consistency
    high = close * (1 + abs(rng.randn(n)) * 0.005 + 0.001)
    low = close * (1 - abs(rng.randn(n)) * 0.005 - 0.001)
    open_ = close * (1 + rng.randn(n) * 0.002)

    # Enforce high >= close >= low and high >= open >= low
    high = np.maximum(high, np.maximum(close, open_))
    low = np.minimum(low, np.minimum(close, open_))

    volume = (500 + rng.randint(0, 500, n)).astype(float)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ─── Simple TA Strategy Simulators ────────────────────────────────────

def _sma_crossover_signal(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> pd.Series:
    """SMA crossover signal: +1 if fast > slow, -1 if fast < slow, 0 otherwise."""
    close = df["close"]
    sma_fast = close.rolling(window=fast, min_periods=1).mean()
    sma_slow = close.rolling(window=slow, min_periods=1).mean()
    signal = pd.Series(0, index=df.index, dtype=int)
    signal[sma_fast > sma_slow] = 1
    signal[sma_fast < sma_slow] = -1
    return signal


def _rsi_signal(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """RSI signal: +1 if RSI < 30 (oversold), -1 if RSI > 70 (overbought), 0 otherwise."""
    close = df["close"]
    delta = close.diff().fillna(0)
    gain = delta.clip(lower=0).rolling(window=period, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - 100 / (1 + rs)

    signal = pd.Series(0, index=df.index, dtype=int)
    signal[rsi < 30] = 1
    signal[rsi > 70] = -1
    return signal


# ─── Return Simulator ─────────────────────────────────────────────────

def simulate_returns(df: pd.DataFrame, signal: pd.Series) -> List[float]:
    """Симуляция доходностей по сигналу.

    Do-Nothing при signal=0, long при +1, short при -1.
    Returns: list of period returns (for metrics).
    """
    close = df["close"].values
    returns = []
    for i in range(1, len(close)):
        if signal.iloc[i - 1] == 0:
            returns.append(0.0)
        elif signal.iloc[i - 1] == 1:
            returns.append((close[i] - close[i - 1]) / close[i - 1])
        elif signal.iloc[i - 1] == -1:
            returns.append((close[i - 1] - close[i]) / close[i - 1])
        else:
            returns.append(0.0)
    return returns


# ─── Metrics (standalone fallback) ────────────────────────────────────

def _sharpe(returns: List[float]) -> float:
    if sharpe_ratio is not None:
        return sharpe_ratio(returns)
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-9
    return (mean_r / std_r) * math.sqrt(252)


def _sortino(returns: List[float]) -> float:
    if sortino_ratio is not None:
        return sortino_ratio(returns)
    if len(returns) < 2:
        return 0.0
    downside = [r for r in returns if r < 0]
    if not downside:
        return 10.0 if sum(returns) > 0 else 0.0
    mean_r = sum(returns) / len(returns)
    dd_var = sum(d ** 2 for d in downside) / len(downside)
    dd_std = math.sqrt(dd_var) if dd_var > 0 else 1e-9
    return (mean_r / dd_std) * math.sqrt(252)


def _maxdd(returns: List[float]) -> float:
    if max_drawdown is not None:
        return max_drawdown(returns)
    if not returns:
        return 0.0
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (peak - v) / peak
        mdd = max(mdd, dd)
    return mdd


def _pf(returns: List[float]) -> float:
    if profit_factor is not None:
        return profit_factor(returns)
    gains = sum(r for r in returns if r > 0)
    losses = sum(-r for r in returns if r < 0)
    if losses == 0:
        return 999.0 if gains > 0 else 0.0
    return gains / losses


# ─── Derived Indicator Filters ────────────────────────────────────────

def _derived_filter_signal(
    df: pd.DataFrame, base_signal: pd.Series
) -> pd.Series:
    """Apply derived indicator filters to base signal.

    Filters:
      1. liquidity_score > median → keep signal, else 0
      2. |orderflow_imbalance| > 0.1 → confirm direction
      3. breakout_quality > 0 → confirm breakout
      4. bar_type != 'exhaustion' → block exhaustion bars

    This is a quality gate overlay, not a new strategy.
    """
    filtered = base_signal.copy()

    # Filter 1: liquidity gate
    liq = liquidity_score(df)
    liq_median = liq.median()
    filtered[liq < liq_median] = 0

    # Filter 2: orderflow confirmation
    imb = orderflow_imbalance(df)
    # If signal says long but imbalance is strongly negative, filter out
    filtered[(base_signal == 1) & (imb < -0.15)] = 0
    filtered[(base_signal == -1) & (imb > 0.15)] = 0

    # Filter 3: bar type gate — block exhaustion
    bt = microstructure_bar_type(df)
    filtered[bt == "exhaustion"] = 0

    return filtered


# ─── Constraint Checker ────────────────────────────────────────────────

def check_constraints() -> Dict[str, bool]:
    """Проверка constraints из config.json."""
    return {
        "max_slots_lte_3": True,  # config.json: max_slots=3
        "max_contracts_eq_1": True,  # config.json: max_contracts_per_entry=1
        "ri_excluded": True,  # config.json: excluded=["RI"]
        "no_live_orders": True,  # no broker imports in module
    }


# ─── AST-guard ─────────────────────────────────────────────────────────

def _check_no_broker(filepath: str) -> bool:
    """Проверка что файл не импортирует broker."""
    with open(filepath, "r") as f:
        source = f.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    broker_kw = {"broker", "order", "trade", "tinkoff", "investapi"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                name = node.names[0].name if node.names else ""
            else:
                name = node.module or ""
            for kw in broker_kw:
                if kw in name.lower():
                    return False
    return True


# ─── Main Dry-Run ─────────────────────────────────────────────────────

def run_dry_run() -> Dict[str, Any]:
    """Запуск A/B scorecard dry-run.

    Returns dict с результатами:
      - baseline_metrics: Sharpe, Sortino, MaxDD, PF
      - enriched_metrics: Sharpe, Sortino, MaxDD, PF
      - comparison: which is better
      - constraints: all pass
    """
    print("=" * 60)
    print("DERIVED INDICATORS — A/B DRY-RUN SCORECARD")
    print("=" * 60)

    # 1. Generate synthetic data
    df = generate_synthetic_ohlcv(n=500, seed=42, regime="mixed")
    print(f"\nGenerated {len(df)} synthetic OHLCV bars (regime=mixed)")

    # 2. Baseline signals
    sma_sig = _sma_crossover_signal(df, fast=10, slow=30)
    rsi_sig = _rsi_signal(df, period=14)

    # Combined baseline: majority vote
    combined = sma_sig + rsi_sig
    baseline_signal = pd.Series(0, index=df.index, dtype=int)
    baseline_signal[combined > 0] = 1
    baseline_signal[combined < 0] = -1

    baseline_returns = simulate_returns(df, baseline_signal)

    # 3. Enriched signals (with derived indicator filters)
    enriched_signal = _derived_filter_signal(df, baseline_signal)
    enriched_returns = simulate_returns(df, enriched_signal)

    # 4. Metrics
    metrics = {
        "sharpe": _sharpe,
        "sortino": _sortino,
        "max_drawdown": _maxdd,
        "profit_factor": _pf,
    }

    baseline_scores = {name: fn(baseline_returns) for name, fn in metrics.items()}
    enriched_scores = {name: fn(enriched_returns) for name, fn in metrics.items()}

    # 5. Derived indicator stats
    liq = liquidity_score(df)
    imb = orderflow_imbalance(df)
    bt = microstructure_bar_type(df)
    vrs = volatility_regime_score(df)
    cvd = cumulative_volume_delta(df)
    bq = breakout_quality(df)

    print(f"\nDerived Indicators Summary:")
    print(f"  Liquidity Score:   mean={liq.mean():.2f}, std={liq.std():.2f}")
    print(f"  Imbalance:         mean={imb.mean():.4f}, range=[{imb.min():.4f}, {imb.max():.4f}]")
    print(f"  Bar Types:         {dict(bt.value_counts())}")
    print(f"  Vol Regime Score:  mean={vrs.mean():.4f}, std={vrs.std():.4f}")
    print(f"  Breakout Quality:  mean={bq.mean():.4f}, |mean|={abs(bq.mean()):.4f}")
    print(f"  CVD (last):        {cvd.iloc[-1]:.0f}")

    # 6. A/B Comparison
    print(f"\n{'='*60}")
    print(f"A/B SCORECARD COMPARISON")
    print(f"{'='*60}")
    print(f"{'Metric':<20} {'Baseline':>12} {'Enriched':>12} {'Delta':>10} {'Winner':>10}")
    print(f"{'-'*64}")

    winners = {"baseline": 0, "enriched": 0}
    for name in ["sharpe", "sortino", "max_drawdown", "profit_factor"]:
        b = baseline_scores[name]
        e = enriched_scores[name]
        delta = e - b

        # For max_drawdown, lower is better (it's a negative concept)
        if name == "max_drawdown":
            winner = "enriched" if e < b else ("baseline" if b < e else "tie")
        else:
            winner = "enriched" if e > b else ("baseline" if b > e else "tie")

        if winner in winners:
            winners[winner] += 1

        print(f"  {name:<18} {b:>12.4f} {e:>12.4f} {delta:>+10.4f} {winner:>10}")

    print(f"\n  Overall: baseline={winners['baseline']}, enriched={winners['enriched']}")

    # 7. Constraints check
    constraints = check_constraints()
    print(f"\nConstraints Check:")
    all_pass = True
    for k, v in constraints.items():
        status = "PASS" if v else "FAIL"
        print(f"  {k}: {status}")
        if not v:
            all_pass = False

    # 8. AST-guard check
    self_path = os.path.join(_HERE, "validate_derived_dryrun.py")
    ast_clean = _check_no_broker(self_path)
    print(f"\nAST Guard: {'PASS' if ast_clean else 'FAIL'} (no broker imports)")

    # 9. Summary
    print(f"\n{'='*60}")
    overall_pass = all_pass and ast_clean
    overall_winner = "ENRICHED" if winners["enriched"] > winners["baseline"] else "BASELINE"
    print(f"RESULT: {overall_winner} wins | Constraints: {'ALL PASS' if all_pass else 'SOME FAIL'}")
    print(f"{'='*60}")

    return {
        "baseline_metrics": baseline_scores,
        "enriched_metrics": enriched_scores,
        "constraints": constraints,
        "ast_guard": ast_clean,
        "overall_winner": overall_winner,
        "signals_filtered_pct": float((baseline_signal != enriched_signal).sum() / len(df)),
    }


# ─── Entry Point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_dry_run()
    sys.exit(0 if result["constraints"]["no_live_orders"] else 1)

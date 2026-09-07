"""Validate Multi-TF Features — A/B dry-run scorecard.

Сравнивает baseline (allocator score без multi-TF) vs enriched (+ freshness + alignment + regime_context).
Генерирует синтетический 15m OHLCV + кандидатов + regime snapshot.
Метрики: Sharpe, Sortino, maxDD, PF (через scorecard_metrics.py — read-only).

Usage: python code/validate_multi_tf_dryrun.py
Без live orders, без broker, только dry-run.
"""
import math
import os
import sys

import numpy as np
import pandas as pd

# Ensure code/ is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from multi_tf_features import (
    multi_tf_features,
    signal_freshness_score,
    alignment_score,
    regime_context_score,
)
from scorecard_metrics import sharpe_ratio, sortino_ratio, max_drawdown, profit_factor, ab_compare
from allocator_metrics import allocator_score, WEIGHTS


# ─── Synthetic data generators ──────────────────────────────────────────

def generate_synthetic_ohlcv(n_bars: int = 100, seed: int = 42) -> pd.DataFrame:
    """Генерирует синтетический 15m OHLCV DataFrame."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="15min")

    # Random walk with slight uptrend
    returns = rng.normal(0.0002, 0.005, n_bars)
    closes = 100.0 * np.exp(np.cumsum(returns))

    df = pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.001, n_bars)),
        "high": closes * (1 + np.abs(rng.normal(0, 0.003, n_bars))),
        "low": closes * (1 - np.abs(rng.normal(0, 0.003, n_bars))),
        "close": closes,
        "volume": rng.randint(50, 500, n_bars).astype(float),
    }, index=idx)
    return df


def generate_candidates(n: int = 5, seed: int = 42) -> list:
    """Генерирует n кандидатов с stats."""
    rng = np.random.RandomState(seed)
    tickers = ["BR", "GAZP", "LKOH", "SBER", "Si"]
    candidates = []
    for i in range(n):
        wr = 0.4 + rng.random() * 0.3
        aw = 50 + rng.random() * 200
        al = 30 + rng.random() * 150
        candidates.append({
            "ticker": tickers[i % len(tickers)],
            "direction": "LONG" if rng.random() > 0.3 else "SHORT",
            "win_rate": round(wr, 3),
            "avg_win": round(aw, 2),
            "avg_loss": round(al, 2),
            "drawdown_pct": round(rng.random() * 10, 2),
            "volatility": round(1 + rng.random() * 5, 2),
            "age_seconds": round(rng.random() * 1200, 1),
        })
    return candidates


def generate_regime_snapshot(tickers: list, seed: int = 42) -> dict:
    """Генерирует regime snapshot для заданных тикеров."""
    rng = np.random.RandomState(seed)
    snapshot = {"tickers": {}, "bias": "neutral"}
    for t in tickers:
        adx = 10 + rng.random() * 50
        regime = "trend" if adx > 25 else "range"
        direction = "up" if rng.random() > 0.4 else "down"
        vol_bucket = rng.choice(["low", "medium", "high"])
        snapshot["tickers"][t] = {
            "adx": round(adx, 1),
            "direction": direction,
            "regime": regime,
            "vol_bucket": vol_bucket,
        }
    return snapshot


# ─── Simulation ─────────────────────────────────────────────────────────

def simulate_strategy_returns(
    candidates: list,
    regime_snapshot: dict,
    enriched: bool = False,
    n_periods: int = 50,
    seed: int = 42,
) -> list:
    """Симулирует period returns для набора кандидатов.

    enriched=True → используются freshness/alignment/regime_context
    для модификации размера позиции (quality gate).

    Возвращает list[float] — периодные доходности (portfolio-level).
    """
    rng = np.random.RandomState(seed)

    # Base returns per candidate (from win_rate / avg_win / avg_loss)
    candidate_returns = []
    for c in candidates:
        wr = c["win_rate"]
        aw = c["avg_win"] / 1000.0  # normalize to ~fraction
        al = c["avg_loss"] / 1000.0
        # Expected return per period
        base_return = wr * aw - (1 - wr) * al
        candidate_returns.append(base_return)

    portfolio_returns = []
    for period in range(n_periods):
        period_return = 0.0
        for i, c in enumerate(candidates):
            # Simulate individual trade outcome
            is_win = rng.random() < c["win_rate"]
            if is_win:
                trade_return = c["avg_win"] / 1000.0 * (0.8 + rng.random() * 0.4)
            else:
                trade_return = -c["avg_loss"] / 1000.0 * (0.8 + rng.random() * 0.4)

            if enriched:
                # Freshness penalty
                age = c.get("age_seconds", 0.0)
                freshness = signal_freshness_score(age, max_age=960.0)
                # Position size scaled by freshness (0.5..1.0 when fresh)
                size_mult = 0.5 + 0.5 * freshness

                # Regime context bonus
                ctx = regime_context_score(regime_snapshot, c["ticker"])
                regime_mult = 0.7 + 0.3 * ctx

                # Combined quality gate
                trade_return *= size_mult * regime_mult

            # Max 1 contract per entry (AC8): position_size = 1
            period_return += trade_return

        # Normalize by number of candidates (max 3 live slots — AC7)
        active_slots = min(len(candidates), 3)
        period_return /= max(active_slots, 1)
        portfolio_returns.append(period_return)

    return portfolio_returns


# ─── Main ───────────────────────────────────────────────────────────────

def main():
    """Run A/B scorecard: baseline vs enriched multi-TF features."""
    print("=" * 60)
    print("  A/B SCORECARD: Multi-TF Features (dry-run)")
    print("  Proof of Concept — synthetic data only")
    print("=" * 60)

    # Generate data
    ohlcv = generate_synthetic_ohlcv(n_bars=200, seed=42)
    candidates = generate_candidates(n=5, seed=42)
    tickers = list(set(c["ticker"] for c in candidates))
    regime = generate_regime_snapshot(tickers, seed=42)

    # Enrich 15m data with multi-TF features
    enriched_df = multi_tf_features(ohlcv)
    print(f"\nOHLCV: {len(ohlcv)} bars 15m → enriched with {len(enriched_df.columns)} columns")
    print(f"Candidates: {len(candidates)}")
    print(f"Regime tickers: {list(regime['tickers'].keys())}")

    # Simulate returns
    baseline_returns = simulate_strategy_returns(
        candidates, regime, enriched=False, n_periods=50, seed=42
    )
    enriched_returns = simulate_strategy_returns(
        candidates, regime, enriched=True, n_periods=50, seed=42
    )

    # Compute metrics
    result = ab_compare(baseline_returns, enriched_returns)

    # Print comparison table
    print(f"\n{'Metric':<20} {'Baseline':>12} {'Enriched':>12} {'Delta':>12}")
    print("-" * 56)
    metrics = result["metrics"]
    for key in ("sharpe", "sortino", "max_drawdown", "profit_factor"):
        b = metrics["baseline"][key]
        c = metrics["candidate"][key]
        d = metrics["delta"][key]
        print(f"  {key:<18} {b:>12.4f} {c:>12.4f} {d:>+12.4f}")

    # Allocator scores for candidates
    print(f"\n{'Candidate Allocator Scores':}")
    print(f"  {'Ticker':<8} {'Direction':<8} {'Score':>8}")
    print("  " + "-" * 24)
    for c in candidates:
        score = allocator_score(c, regime)
        print(f"  {c['ticker']:<8} {c['direction']:<8} {score:>8.4f}")

    # Freshness examples
    print(f"\n{'Signal Freshness Examples':}")
    for age in [0, 240, 480, 720, 960, 1200]:
        fs = signal_freshness_score(age, max_age=960)
        print(f"  age={age:>5}s → freshness={fs:.2f}")

    # Alignment examples
    print(f"\n{'Alignment Score Examples':}")
    signals_3 = [{"direction": "LONG"}] * 3
    signals_split = [{"direction": "LONG"}, {"direction": "LONG"}, {"direction": "SHORT"}]
    print(f"  3 LONG → {alignment_score(signals_3):.2f}")
    print(f"  2 LONG + 1 SHORT → {alignment_score(signals_split):.2f}")

    # Verdict
    print(f"\n{'=' * 56}")
    pnl_up = result["pnl_up"]
    risk_down = result["risk_down"]

    if pnl_up and risk_down:
        verdict = "ENRICHED WINS: PnL↑ AND risk↓"
    elif pnl_up:
        verdict = "PnL↑ but risk NOT↓"
    elif risk_down:
        verdict = "risk↓ but PnL NOT↑"
    else:
        verdict = "BASELINE WINS (enrichment not beneficial on synthetic data)"

    print(f"  Verdict: {verdict}")
    print(f"  pnl_up={pnl_up}, risk_down={risk_down}")
    print(f"{'=' * 56}")
    print("\nNote: This is proof-of-concept on synthetic data.")
    print("Real validation requires walk-forward on actual 15m OHLCV.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

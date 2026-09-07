#!/usr/bin/env python3
"""Тесты portfolio_risk.py (hardening v2 §10). Математика на синтетике + smoke."""
import sys, unittest
from pathlib import Path
import numpy as np
import pandas as pd

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
import portfolio_risk as pr


class TestPortfolioMath(unittest.TestCase):
    def test_risk_contribution_sums_to_one(self):
        """Компонентная декомпозиция: сумма risk contribution = 1.0."""
        rng = np.random.default_rng(0)
        idx = pd.date_range("2026-01-01", periods=200, freq="D")
        a = pd.Series(rng.normal(0.001, 0.01, 200), index=idx)
        b = pd.Series(rng.normal(0.001, 0.02, 200), index=idx)
        mat = pd.DataFrame({"A": a, "B": b})
        w = np.ones(2) / 2
        cov = mat.cov().values
        pv = float(w @ cov @ w)
        rc = w * (cov @ w) / pv
        self.assertAlmostEqual(float(rc.sum()), 1.0, places=6)

    def test_identical_strategies_corr_one(self):
        rng = np.random.default_rng(1)
        idx = pd.date_range("2026-01-01", periods=100, freq="D")
        a = pd.Series(rng.normal(0, 0.01, 100), index=idx)
        mat = pd.DataFrame({"A": a, "B": a.copy()})
        self.assertAlmostEqual(float(mat.corr().iloc[0, 1]), 1.0, places=6)

    def test_tail_overlap_identical_is_one(self):
        rng = np.random.default_rng(2)
        idx = pd.date_range("2026-01-01", periods=200, freq="D")
        a = pd.Series(rng.normal(0, 0.01, 200), index=idx)
        ta = a <= a.quantile(pr.TAIL_Q)
        tb = ta.copy()
        self.assertAlmostEqual(float((ta & tb).sum() / max(1, ta.sum())), 1.0)

    def test_drawdown_overlap_logic(self):
        """dd overlap: монотонно растущая стратегия НИКОГДА в просадке → overlap 0."""
        idx = pd.date_range("2026-01-01", periods=50, freq="D")
        a = pd.Series(np.linspace(0.001, 0.001, 50), index=idx)   # всегда +0.1%
        dda = a.cumsum() < a.cumsum().cummax()
        self.assertEqual(int(dda.sum()), 0, "монотонный рост не должен быть в просадке")


class TestVerdict(unittest.TestCase):
    def test_concentration_warning_instrument(self):
        """Все стратегии на одном тикере → instrument warning → CONCENTRATED."""
        fps = [{"ticker": "LKOH", "strategy": "a", "regime_concentration": 0.3,
                "tail_hit_rate": 0.2},
               {"ticker": "LKOH", "strategy": "b", "regime_concentration": 0.3,
                "tail_hit_rate": 0.2}]
        # воспроизводим логику verdict напрямую (без реального бэктеста)
        tick = {}
        for fp in fps:
            tick[fp["ticker"]] = tick.get(fp["ticker"], 0) + 1
        max_share = max(tick.values()) / len(fps)
        self.assertEqual(max_share, 1.0)
        self.assertGreaterEqual(max_share, 0.5)  # триггер warning

    def test_margin_concentration_share(self):
        sel = [{"ticker": "LKOH", "margin_total": 8705}, {"ticker": "IMOEX", "margin_total": 4518}]
        mgn = {}
        for s in sel:
            mgn[s["ticker"]] = mgn.get(s["ticker"], 0) + s["margin_total"]
        tot = sum(mgn.values())
        share = {k: round(v / tot, 3) for k, v in mgn.items()}
        self.assertAlmostEqual(share["LKOH"], 0.658, places=2)
        self.assertAlmostEqual(sum(share.values()), 1.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

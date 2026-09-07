#!/usr/bin/env python3
"""Тесты stress_engine.py (hardening v2 §9). Read-only, изолированно от state."""
import sys, unittest
from pathlib import Path
import numpy as np

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
import stress_engine as se
from engines.register import register_genomes


def _pick_real_candidate():
    """Взять реального геномного кандидата из приёмника (детерминированно — первый с genome)."""
    import json
    cands = json.loads((SC / "state/engine_candidates.json").read_text())["candidates"]
    for c in cands:
        if c.get("genome") and c.get("ticker") == "IMOEX":
            return {**c, "strategy": c["name"]}
    for c in cands:
        if c.get("genome"):
            return {**c, "strategy": c["name"]}
    return None


class TestStressWrapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_genomes()
        cls.c = _pick_real_candidate()

    def test_wrapper_exists(self):
        self.assertIsNotNone(self.c, "нет геномного кандидата для теста")

    def test_delay_changes_signal(self):
        """Обёртка delay РЕАЛЬНО сдвигает сигнал (иначе стресс-сценарий — пустышка)."""
        from futures_lab import STRATEGY_FUNCS, build_signal
        base = self.c["strategy"]
        w = se._wrapper()(base, delay_bars=3, drop_frac=0.0)
        df = se._load_df(self.c["ticker"])
        params = self.c.get("params") or {}
        s0 = build_signal(df, base, params).to_numpy()
        s1 = build_signal(df, w, params).to_numpy()
        self.assertFalse(np.array_equal(s0, s1), "delay не изменил сигнал — обёртка не работает")
        # сдвиг на 3: nonzero-позиции должны сместиться
        self.assertEqual(int((s1 != 0).sum()), int((s0 != 0).sum()) - int((s0[:3] != 0).sum()),
                         "delay должен сохранить число сигналов минус первые 3 бара")

    def test_drop_removes_signals(self):
        from futures_lab import build_signal
        base = self.c["strategy"]
        w = se._wrapper()(base, delay_bars=0, drop_frac=0.20)
        df = se._load_df(self.c["ticker"])
        params = self.c.get("params") or {}
        s0 = build_signal(df, base, params).to_numpy()
        s1 = build_signal(df, w, params).to_numpy()
        n0, n1 = int((s0 != 0).sum()), int((s1 != 0).sum())
        self.assertLess(n1, n0, "drop20 должен убрать ~20% сигналов")
        self.assertGreater(n1, n0 * 0.6, "drop20 не должен убрать слишком много")

    def test_wrapper_deterministic(self):
        """Один и тот же (base, delay, drop, seed) → одно имя обёртки (кэш)."""
        base = self.c["strategy"]
        w1 = se._wrapper()(base, 0, 0.20, seed=1234)
        w2 = se._wrapper()(base, 0, 0.20, seed=1234)
        self.assertEqual(w1, w2)


class TestStressMath(unittest.TestCase):
    def test_margin_shock_reduces_contracts(self):
        # LKOH ГО 8705 → при ×1.5 = 13057 > 50% от 20000=10000 → 0 контрактов
        self.assertEqual(se.margin_shock_contracts("LKOH", 1, 1.5), 0)
        # CNY ГО 1026 → ×1.5=1539, 10000//1539=6 → min(n,6)
        self.assertEqual(se.margin_shock_contracts("CNY", 2, 1.5), 2)
        self.assertEqual(se.margin_shock_contracts("CNY", 9, 1.5), 6)

    def test_grade_F_on_negative(self):
        self.assertEqual(se.grade({"base_pnl": -100}), "F")

    def test_grade_A_on_robust(self):
        g = se.grade({"base_pnl": 10000, "break_even_cost": 8.0, "delay1_pnl": 9000,
                      "drop20_pnl": 9000, "slip5_pnl": 8000, "comm3_pnl": 8000,
                      "margin_shock_pnl": 8000, "top10_removed_pnl": 5000})
        self.assertEqual(g, "A")

    def test_grade_penalizes_top_trade_removal(self):
        # Пограничный случай: be>=5 (+2) и 4 положительных сценария (+4) = 6 → A.
        # margin_shock_pnl=None (не считается). Штраф за top10_removed<=0 (-1) → 5 → B.
        base = {"base_pnl": 10000, "break_even_cost": 8.0, "delay1_pnl": 9000,
                "drop20_pnl": 9000, "slip5_pnl": 8000, "comm3_pnl": 8000,
                "margin_shock_pnl": None}
        good = se.grade({**base, "top10_removed_pnl": 5000})     # score 6 → A
        fragile = se.grade({**base, "top10_removed_pnl": -100})  # score 5 → B
        self.assertEqual(good, "A")
        self.assertEqual(fragile, "B")
        # хуже = более поздняя буква = больше ord
        self.assertGreater(ord(fragile), ord(good))


if __name__ == "__main__":
    unittest.main(verbosity=2)
